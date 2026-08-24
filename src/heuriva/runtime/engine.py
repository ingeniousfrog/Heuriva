from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from heuriva.config import AppConfig
from heuriva.controller.base import Controller
from heuriva.core.common import new_id
from heuriva.core.decision import Decision
from heuriva.core.event import EventLevel, RuntimeEvent
from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, StateStatus
from heuriva.core.state_patch import OperationResult
from heuriva.runtime.executor_router import ExecutorRouter
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


ProgressCallback = Callable[[RuntimeProgress], None]


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

    def run(
        self,
        task: str,
        *,
        trace: bool = False,
        progress: ProgressCallback | None = None,
    ) -> RuntimeResult:
        goal = task.strip()
        if not goal:
            raise ValueError("task must not be empty")
        task_id = new_id()
        state = CognitiveState.new(task_id=task_id, goal=goal)
        self.store.create_task_with_trajectory(
            state, config_snapshot=self.config.redacted_snapshot()
        )
        started = time.monotonic()
        steps: list[RuntimeStep] = []
        trace_lines: list[str] = []
        consecutive_failures = 0
        final_answer: str | None = None
        status = StateStatus.MAX_STEPS_REACHED
        termination_reason = "max_steps_reached"
        self._notify_progress(
            progress,
            task_id=task_id,
            step_index=state.step_index,
            stage="task_started",
            message="started task",
            started=started,
        )
        try:
            for _ in range(self.config.runtime.max_steps):
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
                available = self._available_for_state(state)
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
                        policy_hints=(),
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
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="executor_running",
                        message=f"executing {decision.operator.value}; waiting for result",
                        operator=decision.operator.value,
                        started=started,
                    )
                    result = executor.execute(decision, state)
                except Exception as exc:
                    event = RuntimeEvent(
                        task_id=state.task_id,
                        step_index=state.step_index,
                        event_type="runtime_error",
                        level=EventLevel.ERROR,
                        payload={"error": exc.__class__.__name__, "message": str(exc)[:500]},
                    )
                    self.store.log_event(event)
                    status = StateStatus.FAILED
                    termination_reason = "runtime_error"
                    self._notify_progress(
                        progress,
                        task_id=task_id,
                        step_index=state.step_index,
                        stage="runtime_error",
                        message=f"runtime error: {exc.__class__.__name__}; finalizing as failed",
                        started=started,
                    )
                    break
                observation = self._observation_from_result(
                    decision=decision,
                    executor_kind=executor_kind,
                    result=result,
                )
                state_after = self.updater.apply(state, result.patch, history_ref=observation.id)
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
                )
                trace_lines.append(line)
                steps.append(
                    RuntimeStep(decision=decision, observation=observation, state=state_after)
                )
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
                steps=steps,
                trace_lines=trace_lines,
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
            raise

    def _available_for_state(self, state: CognitiveState) -> tuple[Operator, ...]:
        available = self.router.available_operators()
        remaining = self.config.runtime.max_steps - state.step_index
        if remaining <= 1:
            return (Operator.ANSWER,)
        return available

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
