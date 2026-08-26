from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from heuriva.clients.model import ModelClientError
from heuriva.config import AppConfig
from heuriva.controller.base import Controller
from heuriva.core.common import new_id
from heuriva.core.decision import Decision
from heuriva.core.event import EventLevel, RuntimeEvent
from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, StateStatus
from heuriva.core.state_patch import OperationResult
from heuriva.core.task_contract import (
    CriterionInput,
    EvidenceRequirement,
    SearchPolicy,
    TaskContract,
)
from heuriva.redaction import redact_text
from heuriva.runtime.completion_validation import CompletionValidationResult, CompletionValidator
from heuriva.runtime.executor_router import ExecutorRouter
from heuriva.runtime.progress_policy import ProgressPolicyResult, evaluate_progress_policy
from heuriva.runtime.resume import (
    ResumeEligibility,
    assess_resume_eligibility,
    config_snapshot_warnings,
)
from heuriva.runtime.search_policy import SearchGuardResult, evaluate_search_guard
from heuriva.runtime.state_delta import StateDelta, calculate_state_delta
from heuriva.runtime.state_updater import StateUpdater
from heuriva.storage.sqlite import SQLiteStore
from heuriva.trace import render_step


class Executor(Protocol):
    def execute(self, decision: Decision, state: CognitiveState) -> OperationResult: ...


@dataclass(frozen=True)
class RuntimeStep:
    decision: Decision
    observation: Observation
    state: CognitiveState
    state_before: CognitiveState | None = None
    state_delta: StateDelta | None = None


@dataclass(frozen=True)
class RuntimeProgress:
    task_id: str
    step_index: int
    stage: str
    message: str
    elapsed_seconds: float
    operator: str | None = None


@dataclass(frozen=True)
class RuntimeResult:
    task_id: str
    status: str
    final_answer: str | None
    steps: list[RuntimeStep]
    trace_lines: list[str]
    resumed: bool = False


class ResumeRejected(ValueError):
    def __init__(self, eligibility: ResumeEligibility) -> None:
        self.eligibility = eligibility
        super().__init__(
            f"cannot resume task {eligibility.task_id}: {eligibility.reason} "
            f"(status={eligibility.task_status})"
        )


class RuntimeInterrupted(KeyboardInterrupt):
    def __init__(self, task_id: str) -> None:
        super().__init__(task_id)
        self.task_id = task_id


ProgressCallback = Callable[[RuntimeProgress], None]
InterruptCheck = Callable[[], bool]


class RuntimeEngine:
    def __init__(
        self,
        *,
        config: AppConfig,
        store: SQLiteStore,
        controller: Controller,
        executors: Mapping[Operator, Executor],
    ) -> None:
        self.config = config
        self.store = store
        self.controller = controller
        self.executors = executors
        self.router = ExecutorRouter(search_enabled=config.tools.search.enabled)
        self.updater = StateUpdater()
        self.completion_validator = CompletionValidator(config.quality)

    def cancel_io(self) -> None:
        """Best-effort abort of in-flight model HTTP (Session Interrupt)."""
        for client in self._iter_model_clients():
            abort = getattr(client, "abort", None)
            if callable(abort):
                abort()
            else:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

    def _iter_model_clients(self) -> list[Any]:
        clients: list[Any] = []
        seen: set[int] = set()
        for owner in (self.controller, *self.executors.values()):
            client = getattr(owner, "model_client", None)
            if client is None:
                continue
            marker = id(client)
            if marker in seen:
                continue
            seen.add(marker)
            clients.append(client)
        return clients

    def run(
        self,
        task: str,
        *,
        trace: bool = False,
        progress: ProgressCallback | None = None,
        interrupt_check: InterruptCheck | None = None,
        criteria: tuple[CriterionInput, ...] = (),
        search_policy: SearchPolicy | str = SearchPolicy.AUTO,
        evidence_requirement: EvidenceRequirement | str = EvidenceRequirement.OPTIONAL,
    ) -> RuntimeResult:
        goal = task.strip()
        if not goal:
            raise ValueError("task must not be empty")
        task_id = new_id()
        task_contract = TaskContract.from_user(
            criteria=criteria,
            search_policy=search_policy,
            evidence_requirement=evidence_requirement,
        )
        state = CognitiveState.new(task_id=task_id, goal=goal, task_contract=task_contract)
        self.store.create_task_with_trajectory(
            state, config_snapshot=self.config.redacted_snapshot()
        )
        return self._run_loop(
            task_id=task_id,
            state=state,
            prior_steps=(),
            trace=trace,
            progress=progress,
            interrupt_check=interrupt_check,
            resumed=False,
            start_message="started task",
        )

    def resume(
        self,
        task_id: str,
        *,
        force: bool = False,
        trace: bool = False,
        progress: ProgressCallback | None = None,
        interrupt_check: InterruptCheck | None = None,
    ) -> RuntimeResult:
        cleaned = task_id.strip()
        if not cleaned:
            raise ValueError("task_id must not be empty")
        try:
            bundle = self.store.load_resume_bundle(cleaned)
        except KeyError as exc:
            raise KeyError(cleaned) from exc
        warnings = config_snapshot_warnings(
            bundle["config_snapshot"],
            self.config.redacted_snapshot(),
        )
        eligibility = assess_resume_eligibility(
            task_id=cleaned,
            task_status=str(bundle["task_status"]),
            step_count=len(bundle["steps"]),
            force=force,
            has_latest_state=True,
            config_warnings=warnings,
        )
        if not eligibility.eligible:
            raise ResumeRejected(eligibility)
        latest_state: CognitiveState = bundle["latest_state"]
        prior_steps = self._runtime_steps_from_bundle(bundle["steps"])
        resume_state = latest_state
        if resume_state.status is not StateStatus.RUNNING:
            resume_state = resume_state._replace(
                status=StateStatus.RUNNING,
                revision_index=resume_state.revision_index + 1,
            )
        self.store.prepare_resume(
            task_id=cleaned,
            resume_state=resume_state,
            reason="resumed",
        )
        self.store.log_event(
            RuntimeEvent(
                task_id=cleaned,
                step_index=resume_state.step_index,
                event_type="task_resumed",
                level=EventLevel.INFO,
                payload={
                    "previous_status": eligibility.task_status,
                    "reason": eligibility.reason,
                    "force": force,
                    "step_count": eligibility.step_count,
                    "warnings": list(eligibility.warnings),
                },
            )
        )
        warning_text = ""
        if eligibility.warnings:
            warning_text = f"; warnings={', '.join(eligibility.warnings)}"
        return self._run_loop(
            task_id=cleaned,
            state=resume_state,
            prior_steps=prior_steps,
            trace=trace,
            progress=progress,
            interrupt_check=interrupt_check,
            resumed=True,
            start_message=(
                f"resumed task from status={eligibility.task_status} "
                f"with {eligibility.step_count} committed steps{warning_text}"
            ),
        )

    def _run_loop(
        self,
        *,
        task_id: str,
        state: CognitiveState,
        prior_steps: tuple[RuntimeStep, ...],
        trace: bool,
        progress: ProgressCallback | None,
        interrupt_check: InterruptCheck | None,
        resumed: bool,
        start_message: str,
    ) -> RuntimeResult:
        started = time.monotonic()
        steps: list[RuntimeStep] = list(prior_steps)
        new_steps: list[RuntimeStep] = []
        trace_lines: list[str] = []
        consecutive_failures = 0
        final_answer: str | None = None
        status = StateStatus.MAX_STEPS_REACHED
        termination_reason = "max_steps_reached"
        remaining_budget = max(0, self.config.runtime.max_steps - state.step_index)
        self._notify_progress(
            progress,
            task_id=task_id,
            step_index=state.step_index,
            stage="task_started" if not resumed else "task_resumed",
            message=start_message,
            started=started,
        )
        try:
            for _ in range(remaining_budget):
                self._raise_if_interrupted(interrupt_check)
                if time.monotonic() - started > self.config.runtime.max_task_seconds:
                    status = StateStatus.FAILED
                    termination_reason = "max_task_seconds"
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="task_limit_reached",
                        message="task exceeded max_task_seconds; finalizing as failed",
                        started=started,
                    )
                    break
                policy = self._progress_policy_for_state(state, tuple(steps))
                available = policy.available_operators
                if policy.guard_action is not None:
                    event = RuntimeEvent(
                        task_id=state.task_id,
                        step_index=state.step_index,
                        event_type="loop_guard_applied",
                        level=EventLevel.WARNING,
                        payload=policy.event_payload(),
                    )
                    self.store.log_event(event)
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="loop_guard_applied",
                        message=(
                            f"{policy.guard_reason}; next operators: "
                            f"{', '.join(operator.value for operator in available)}"
                        ),
                        started=started,
                    )
                available_names = ", ".join(operator.value for operator in available)
                self._notify_progress(
                    progress,
                    task_id=task_id,
                    step_index=state.step_index,
                    stage="controller_selecting",
                    message=f"selecting next operator from {available_names}",
                    started=started,
                )
                try:
                    decision, events = self.controller.select(
                        state=state,
                        available_operators=available,
                        runtime_limits=self.config.runtime.model_dump(mode="json"),
                        policy_hints=policy.policy_hints,
                    )
                    for event in events:
                        self.store.log_event(event)
                        if event.level is EventLevel.WARNING:
                            self._notify_progress(
                                progress,
                                task_id=task_id,
                                step_index=state.step_index,
                                stage="controller_warning",
                                message=(
                                    f"controller warning: {event.event_type}; "
                                    "repaired internally and continuing"
                                ),
                                started=started,
                            )
                    executor_kind = self.router.resolve(decision)
                    executor = self.executors[decision.operator]
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="operator_selected",
                        message=(
                            f"selected {decision.operator.value}; "
                            f"next: execute with {executor_kind}"
                        ),
                        operator=decision.operator.value,
                        started=started,
                    )
                    guard = self._search_guard_for_decision(
                        state=state,
                        decision=decision,
                        committed_steps=tuple(steps),
                    )
                    if guard is not None:
                        self.store.log_event(
                            RuntimeEvent(
                                task_id=state.task_id,
                                step_index=state.step_index,
                                event_type="search_guard_applied",
                                level=EventLevel.WARNING,
                                payload=guard.payload,
                            )
                        )
                        self._notify_progress(
                            progress,
                            task_id=task_id,
                            step_index=state.step_index,
                            stage="search_guard_applied",
                            message=(
                                f"{guard.reason}; next operators: "
                                f"{self._operator_names(guard.available_operators)}"
                            ),
                            operator=decision.operator.value,
                            started=started,
                        )
                        result = self._guarded_search_result(guard)
                    else:
                        self._notify_progress(
                            progress,
                            task_id=task_id,
                            step_index=state.step_index,
                            stage="executor_running",
                            message=f"executing {decision.operator.value}; waiting for result",
                            operator=decision.operator.value,
                            started=started,
                        )
                        self._raise_if_interrupted(interrupt_check)
                        result = executor.execute(decision, state)
                        self._raise_if_interrupted(interrupt_check)
                except Exception as exc:
                    event = RuntimeEvent(
                        task_id=state.task_id,
                        step_index=state.step_index,
                        event_type="runtime_error",
                        level=EventLevel.ERROR,
                        payload=self._runtime_error_payload(exc),
                    )
                    self.store.log_event(event)
                    status = StateStatus.FAILED
                    termination_reason = "runtime_error"
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="runtime_error",
                        message=(
                            f"runtime error: {self._runtime_error_summary(exc)}; "
                            "finalizing as failed"
                        ),
                        started=started,
                    )
                    break
                completion = self._validate_completion_result(
                    result=result,
                    decision=decision,
                    state=state,
                    committed_steps=tuple(steps),
                )
                if completion.assessment is not None:
                    self.store.log_event(
                        RuntimeEvent(
                            task_id=state.task_id,
                            step_index=state.step_index,
                            event_type="completion_assessed",
                            level=EventLevel.INFO
                            if completion.result.error is None
                            else EventLevel.WARNING,
                            payload=completion.assessment.model_dump(mode="json"),
                        )
                    )
                result = completion.result
                observation = self._observation_from_result(
                    decision=decision,
                    executor_kind=executor_kind,
                    result=result,
                )
                state_after = self.updater.apply(state, result.patch, history_ref=observation.id)
                state_delta = calculate_state_delta(state, state_after)
                if result.error is not None and result.error.code == "answer_validation_error":
                    self.store.log_event(
                        RuntimeEvent(
                            task_id=state.task_id,
                            step_index=state.step_index,
                            event_type="answer_validation_error",
                            level=EventLevel.WARNING,
                            payload={
                                "code": result.error.code,
                                "message": result.error.message,
                                "retryable": result.error.retryable,
                            },
                        )
                    )
                if result.error is not None:
                    consecutive_failures += 1
                    observation_status_terminal = not result.error.retryable
                    if (
                        observation_status_terminal
                        or consecutive_failures >= self.config.runtime.max_consecutive_failures
                    ):
                        state_after = state_after._replace(status=StateStatus.FAILED)
                        status = StateStatus.FAILED
                        termination_reason = result.error.code
                else:
                    consecutive_failures = 0
                if (
                    decision.operator is Operator.ANSWER
                    and result.final_answer
                    and result.final_answer.strip()
                ):
                    final_answer = result.final_answer.strip()
                    state_after = state_after._replace(status=StateStatus.DONE)
                    state_delta = calculate_state_delta(state, state_after)
                    status = StateStatus.DONE
                    termination_reason = "answer"
                self.store.commit_step(
                    state_before=state,
                    decision=decision,
                    observation=observation,
                    state_after=state_after,
                )
                line = render_step(
                    decision=decision,
                    observation_content=observation.content,
                    trace=trace,
                    state_delta=state_delta,
                )
                trace_lines.append(line)
                runtime_step = RuntimeStep(
                    decision=decision,
                    observation=observation,
                    state=state_after,
                    state_before=state,
                    state_delta=state_delta,
                )
                steps.append(runtime_step)
                new_steps.append(runtime_step)
                next_action = "continue planning"
                if status in {StateStatus.DONE, StateStatus.FAILED}:
                    next_action = "finalize task"
                self._notify_progress(
                    progress,
                    task_id=task_id,
                    step_index=decision.step_index,
                    stage="step_committed",
                    message=(
                        f"committed {decision.operator.value} step with "
                        f"{observation.status.value}; next: {next_action}"
                    ),
                    operator=decision.operator.value,
                    started=started,
                )
                state = state_after
                if status in {StateStatus.DONE, StateStatus.FAILED}:
                    break
            else:
                status = StateStatus.MAX_STEPS_REACHED
                termination_reason = "max_steps_reached"
            if status is StateStatus.MAX_STEPS_REACHED and state.status is StateStatus.RUNNING:
                state = state.terminal(status=StateStatus.MAX_STEPS_REACHED)
            self.store.finalize_task(
                task_id=task_id,
                final_state=state,
                status=status.value,
                termination_reason=termination_reason,
                final_answer=final_answer,
            )
            self._notify_progress(
                progress,
                task_id=task_id,
                step_index=state.step_index,
                stage="task_finished",
                message=(
                    f"finished with status={status.value}; "
                    f"use `heuriva show --trace {task_id}` to inspect saved trajectory"
                ),
                started=started,
            )
            return RuntimeResult(
                task_id=task_id,
                status=status.value,
                final_answer=final_answer,
                steps=new_steps if resumed else steps,
                trace_lines=trace_lines,
                resumed=resumed,
            )
        except KeyboardInterrupt:
            terminal = state.terminal(status=StateStatus.INTERRUPTED)
            self.store.log_event(
                RuntimeEvent(
                    task_id=task_id,
                    step_index=state.step_index,
                    event_type="interrupted",
                    level=EventLevel.WARNING,
                )
            )
            self.store.finalize_task(
                task_id=task_id,
                final_state=terminal,
                status=StateStatus.INTERRUPTED.value,
                termination_reason="keyboard_interrupt",
                final_answer=None,
            )
            raise RuntimeInterrupted(task_id) from None

    @staticmethod
    def _runtime_steps_from_bundle(step_dicts: list[dict[str, object]]) -> tuple[RuntimeStep, ...]:
        rebuilt: list[RuntimeStep] = []
        for item in step_dicts:
            decision = Decision.model_validate(item["decision"])
            observation = Observation.model_validate(item["observation"])
            state_before = CognitiveState.model_validate(item["state_before"])
            state_after = CognitiveState.model_validate(item["state_after"])
            rebuilt.append(
                RuntimeStep(
                    decision=decision,
                    observation=observation,
                    state=state_after,
                    state_before=state_before,
                    state_delta=calculate_state_delta(state_before, state_after),
                )
            )
        return tuple(rebuilt)

    def _progress_policy_for_state(
        self, state: CognitiveState, steps: tuple[RuntimeStep, ...]
    ) -> ProgressPolicyResult:
        return evaluate_progress_policy(
            state=state,
            committed_steps=steps,
            base_available=self.router.available_operators(),
            max_steps=self.config.runtime.max_steps,
            max_same_operator_streak=self.config.runtime.max_same_operator_streak,
            max_no_progress_steps=self.config.runtime.max_no_progress_steps,
            answer_reserve_steps=self.config.runtime.answer_reserve_steps,
        )

    def _search_guard_for_decision(
        self,
        *,
        state: CognitiveState,
        decision: Decision,
        committed_steps: tuple[RuntimeStep, ...],
    ) -> SearchGuardResult | None:
        if decision.operator is not Operator.SEARCH:
            return None
        return evaluate_search_guard(
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=self.config.quality,
            base_available=self.router.available_operators(),
        )

    @staticmethod
    def _guarded_search_result(guard: SearchGuardResult) -> OperationResult:
        return OperationResult(
            content=f"SEARCH blocked by runtime policy: {guard.reason}",
            metadata={"search_guard": guard.payload},
        )

    @staticmethod
    def _operator_names(operators: tuple[Operator, ...]) -> str:
        return ", ".join(operator.value for operator in operators)

    def _validate_completion_result(
        self,
        *,
        result: OperationResult,
        decision: Decision,
        state: CognitiveState,
        committed_steps: tuple[RuntimeStep, ...],
    ) -> CompletionValidationResult:
        if decision.operator is not Operator.ANSWER or result.error is not None:
            return CompletionValidationResult(result=result, assessment=None)
        if result.final_answer is None and not result.content.strip():
            return CompletionValidationResult(result=result, assessment=None)
        return self.completion_validator.validate(
            result=result,
            state=state,
            previous_completion_failures=self._completion_failure_count(committed_steps),
        )

    @staticmethod
    def _completion_failure_count(committed_steps: tuple[RuntimeStep, ...]) -> int:
        count = 0
        for step in committed_steps:
            error = step.observation.error
            if error is not None and error.code in {
                "completion_validation_error",
                "completion_not_met",
            }:
                count += 1
        return count

    @staticmethod
    def _raise_if_interrupted(interrupt_check: InterruptCheck | None) -> None:
        if interrupt_check is not None and interrupt_check():
            raise KeyboardInterrupt

    @staticmethod
    def _notify_progress(
        progress: ProgressCallback | None,
        *,
        task_id: str,
        step_index: int,
        stage: str,
        message: str,
        started: float,
        operator: str | None = None,
    ) -> None:
        if progress is None:
            return
        progress(
            RuntimeProgress(
                task_id=task_id,
                step_index=step_index,
                stage=stage,
                message=message,
                elapsed_seconds=max(0.0, time.monotonic() - started),
                operator=operator,
            )
        )

    @staticmethod
    def _observation_from_result(
        *,
        decision: Decision,
        executor_kind: str,
        result: OperationResult,
    ) -> Observation:
        if result.error is not None:
            kind = ObservationKind.EXECUTOR_ERROR
            status = ObservationStatus.ERROR
        elif decision.operator is Operator.SEARCH:
            kind = ObservationKind.SEARCH_RESULTS
            status = ObservationStatus.SUCCESS
        elif decision.operator is Operator.ANSWER:
            kind = ObservationKind.ANSWER
            status = ObservationStatus.SUCCESS
        else:
            kind = ObservationKind.ANALYSIS
            status = ObservationStatus.SUCCESS
        return Observation(
            task_id=decision.task_id,
            decision_id=decision.id,
            kind=kind,
            status=status,
            content=result.content,
            data={},
            citations=result.citations,
            error=result.error,
            executor_kind=executor_kind,
            metadata=result.metadata,
        )

    @staticmethod
    def _runtime_error_payload(exc: Exception) -> dict[str, object]:
        if isinstance(exc, ModelClientError):
            return {
                "error": exc.__class__.__name__,
                "message": redact_text(exc.message)[:500],
                "code": exc.code,
                "retryable": exc.retryable,
            }
        return {
            "error": exc.__class__.__name__,
            "message": redact_text(str(exc))[:500],
        }

    @staticmethod
    def _runtime_error_summary(exc: Exception) -> str:
        if isinstance(exc, ModelClientError):
            return f"{exc.__class__.__name__} {exc.code}: {redact_text(exc.message)[:180]}"
        message = redact_text(str(exc))[:180]
        if not message:
            return exc.__class__.__name__
        return f"{exc.__class__.__name__}: {message}"
