from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heuriva.clients.search import SearchResult
from heuriva.config import AppConfig, QualityConfig
from heuriva.core.decision import Decision, DecisionDraft, SearchParams, bind_decision
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, EvidenceItem
from heuriva.core.state_patch import OperationResult, StatePatch
from heuriva.core.task_contract import SearchPolicy, SourceScope
from heuriva.executors.search import SearchExecutor
from heuriva.runtime.engine import RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import FakeController, FakeExecutor, make_answer_decision


@dataclass(frozen=True)
class HarnessOutcome:
    trajectory: dict[str, Any]
    search_provider_calls: int = 0
    notes: str = ""
    disagreement_bucket: str | None = None
    judge_verdict: str | None = None


HarnessFn = Callable[[Path], HarnessOutcome]


def run_harness(name: str, workdir: Path) -> HarnessOutcome:
    try:
        runner = HARNESSES[name]
    except KeyError as exc:
        raise ValueError(f"unknown eval harness: {name}") from exc
    return runner(workdir)


def harness_forbidden_search_guard(workdir: Path) -> HarnessOutcome:
    class CountingSearchExecutor:
        calls = 0

        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            self.calls += 1
            return OperationResult(content="provider called")

    search_executor = CountingSearchExecutor()
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(workdir, max_steps=2, answer_reserve_steps=1),
        store=store,
        controller=FakeController(
            [_search_draft("heuriva pricing"), make_answer_decision("Answer")]
        ),
        executors={
            Operator.SEARCH: search_executor,
            Operator.ANSWER: FakeExecutor("final", final_answer="No external search used."),
        },
    )
    result = engine.run("Explain local state", search_policy=SearchPolicy.FORBIDDEN)
    return HarnessOutcome(
        trajectory=store.get_trajectory(result.task_id),
        search_provider_calls=search_executor.calls,
    )


def harness_duplicate_query_guard(workdir: Path) -> HarnessOutcome:
    class CountingSearchExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision
            self.calls += 1
            evidence = EvidenceItem(
                content="Heuriva source: release note",
                source_type="search",
                source_ref="https://example.com/heuriva",
                query="heuriva release",
                relevance_verdict="relevant",
                supports_criteria=("release identified",),
                assessment_origin="deterministic",
            )
            return OperationResult(
                content="1. Heuriva source - https://example.com/heuriva",
                patch=StatePatch(evidence_add=(evidence,)),
                metadata={"accepted_evidence_count": 1, "raw_candidate_count": 1},
            )

    search_executor = CountingSearchExecutor()
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            max_steps=4,
            max_same_operator_streak=5,
            max_no_progress_steps=3,
            answer_reserve_steps=1,
        ),
        store=store,
        controller=FakeController(
            [
                _search_draft("heuriva release"),
                _search_draft("  Heuriva   release "),
                make_answer_decision("Answer"),
            ]
        ),
        executors={
            Operator.SEARCH: search_executor,
            Operator.ANSWER: FakeExecutor("final", final_answer="Done"),
        },
    )
    result = engine.run("Explain the Heuriva release")
    return HarnessOutcome(
        trajectory=store.get_trajectory(result.task_id),
        search_provider_calls=search_executor.calls,
    )


def harness_irrelevant_candidate_rejected(workdir: Path) -> HarnessOutcome:
    del workdir

    class FakeSearchClient:
        def search(self, query: str) -> tuple[tuple[SearchResult, ...], None]:
            assert query == "heuriva release"
            return (
                (
                    SearchResult(
                        title="Weather forecast",
                        url="https://example.com/weather",
                        snippet="Rain tomorrow",
                        rank=1,
                    ),
                ),
                None,
            )

    state = CognitiveState.new(task_id="task-irrelevant", goal="Explain the Heuriva release")
    decision = bind_decision(_search_draft("heuriva release"), state)
    executor = SearchExecutor(
        search_client=FakeSearchClient(),
        quality_config=QualityConfig.model_validate({"evidence_relevance_mode": "enforce"}),
    )
    result = executor.execute(decision, state)
    trajectory = {
        "trajectory": {
            "task_id": state.task_id,
            "status": "running",
            "termination_reason": None,
        },
        "steps": [
            {
                "decision": {"operator": "SEARCH"},
                "observation": {
                    "metadata": {
                        "raw_candidate_count": result.metadata.get("raw_candidate_count", 0),
                        "accepted_evidence_count": result.metadata.get(
                            "accepted_evidence_count", 0
                        ),
                        "rejected_candidate_count": result.metadata.get(
                            "rejected_candidate_count", 0
                        ),
                        "candidate_assessments": result.metadata.get("candidate_assessments", ()),
                    }
                },
                "state_delta": {
                    "added_evidence_count": len(result.patch.evidence_add) if result.patch else 0
                },
            }
        ],
        "events": [],
    }
    return HarnessOutcome(trajectory=trajectory, search_provider_calls=1)


def harness_completion_enforce_block(workdir: Path) -> HarnessOutcome:
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            quality={"completion_check_mode": "enforce", "max_completion_repairs": 0},
        ),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="A short answer")},
    )
    result = engine.run("Explain safety tradeoffs", criteria=("mention safety",))
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_completion_enforce_repair(workdir: Path) -> HarnessOutcome:
    class RepairingAnswerExecutor:
        def __init__(self) -> None:
            self.answers = ["A short answer", "A safe answer that mentions safety"]
            self.calls = 0

        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            answer = self.answers[self.calls]
            self.calls += 1
            return OperationResult(content=answer, final_answer=answer)

    store = SQLiteStore(workdir / "memory.db")
    executor = RepairingAnswerExecutor()
    engine = RuntimeEngine(
        config=_config(
            workdir,
            quality={"completion_check_mode": "enforce", "max_completion_repairs": 1},
        ),
        store=store,
        controller=FakeController(
            [
                make_answer_decision("First answer"),
                make_answer_decision("Repair answer"),
            ]
        ),
        executors={Operator.ANSWER: executor},
    )
    result = engine.run("Explain safety", criteria=("mention safety",))
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_exact_answer_extra_text(workdir: Path) -> HarnessOutcome:
    """Post-v0.5 leak class: exact_answer must fail when extra tokens appear."""
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            quality={"completion_check_mode": "observe"},
        ),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={
            Operator.ANSWER: FakeExecutor(
                "final",
                final_answer="OK\nV03BLOCKTOKEN",
            )
        },
    )
    result = engine.run(
        "Return exactly OK and no other text.",
        criteria=("exact_answer:OK",),
    )
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_must_not_include(workdir: Path) -> HarnessOutcome:
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            quality={"completion_check_mode": "observe"},
        ),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={
            Operator.ANSWER: FakeExecutor(
                "final",
                final_answer="Status is OK but also SECRETTOKEN leaked.",
            )
        },
    )
    result = engine.run(
        "Return a status without secrets.",
        criteria=("must_include:OK", "must_not_include:SECRETTOKEN"),
    )
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_legacy_string_criterion(workdir: Path) -> HarnessOutcome:
    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            quality={"completion_check_mode": "observe"},
        ),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={
            Operator.ANSWER: FakeExecutor(
                "final",
                final_answer="A safe answer that mentions safety clearly.",
            )
        },
    )
    result = engine.run("Explain safety", criteria=("mention safety",))
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_cited_but_off_task(workdir: Path) -> HarnessOutcome:
    from heuriva.runtime.answer_validation import validate_answer_citations

    class CitingAnswerExecutor:
        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision
            answer = "Heuriva exists. [S1]"
            validation = validate_answer_citations(answer, state)
            if not validation.ok:
                assert validation.error is not None
                return OperationResult(
                    content=answer,
                    error=validation.error,
                    metadata={"citation_validation": "failed"},
                )
            return OperationResult(
                content=validation.rendered_answer,
                final_answer=validation.rendered_answer,
                citations=validation.citations,
                metadata={
                    "citation_validation": "passed",
                    "citation_count": len(validation.citations),
                },
            )

    store = SQLiteStore(workdir / "memory.db")
    engine = RuntimeEngine(
        config=_config(
            workdir,
            max_steps=3,
            answer_reserve_steps=1,
            quality={"completion_check_mode": "observe"},
        ),
        store=store,
        controller=FakeController(
            [
                _search_draft("heuriva release"),
                make_answer_decision("Answer"),
            ]
        ),
        executors={
            Operator.SEARCH: FakeExecutor(
                "search",
                evidence_url="https://example.com/heuriva",
            ),
            Operator.ANSWER: CitingAnswerExecutor(),
        },
    )
    result = engine.run(
        "Explain the Heuriva release with safety notes",
        criteria=("mention safety",),
    )
    return HarnessOutcome(trajectory=store.get_trajectory(result.task_id))


def harness_judge_manual_review_needed(workdir: Path) -> HarnessOutcome:
    """Force a fake judge to emit manual_review_needed without a live model."""
    import json

    from heuriva.clients.model import ModelChatResult
    from heuriva.core.decision import AnswerParams
    from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
    from heuriva.eval_judge import FreshJudge
    from heuriva.evaluation import evaluate_with_judge

    store = SQLiteStore(workdir / "memory.db")
    state = CognitiveState.new(task_id="task-judge-manual", goal="Ambiguous local summary")
    store.create_task_with_trajectory(state, config_snapshot={"model": "fake"})
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANSWER,
            objective="answer",
            reason="done",
            success_criteria=("final",),
            params=AnswerParams(),
            confidence=0.5,
        ),
        state,
    )
    observation = Observation(
        task_id=state.task_id,
        decision_id=decision.id,
        kind=ObservationKind.ANSWER,
        status=ObservationStatus.SUCCESS,
        content="Maybe yes, maybe no.",
        executor_kind="llm",
        metadata={
            "citation_validation": "passed",
            "completion_assessment": {
                "verdict": "pass",
                "failed_criteria": [],
                "assessment_origin": "deterministic",
            },
        },
    )
    next_state = state.advance(status="done")
    store.commit_step(
        state_before=state,
        decision=decision,
        observation=observation,
        state_after=next_state,
    )
    store.finalize_task(
        task_id=state.task_id,
        final_state=next_state,
        status="done",
        termination_reason="answer",
        final_answer="Maybe yes, maybe no.",
    )
    data = store.get_trajectory(state.task_id)

    class ScriptedJudgeModel:
        def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
            del messages
            return ModelChatResult(
                content=json.dumps(
                    {
                        "verdict": "insufficient_evidence",
                        "reason": "answer is ambiguous and needs human review",
                        "failed_criteria": [],
                        "manual_review_needed": True,
                    }
                ),
                metadata={"attempt_count": 1, "model": "fake-judge"},
            )

    judged = evaluate_with_judge(
        data,
        judge=FreshJudge(
            model_client=ScriptedJudgeModel(),
            model_name="fake-judge",
            base_url="http://localhost:8765/v1",
            repair_attempts=0,
        ),
        store=store,
        persist=True,
        case_id="judge_manual_review_needed",
    )
    return HarnessOutcome(
        trajectory=store.get_trajectory(state.task_id),
        notes=(
            "fake judge forced manual_review_needed; disagreement is a separate "
            "state, not fail; not product proof"
        ),
        disagreement_bucket=judged.disagreement.bucket.value,
        judge_verdict=judged.judge.verdict.value,
    )


HARNESSES: dict[str, HarnessFn] = {
    "forbidden_search_guard": harness_forbidden_search_guard,
    "duplicate_query_guard": harness_duplicate_query_guard,
    "irrelevant_candidate_rejected": harness_irrelevant_candidate_rejected,
    "completion_enforce_block": harness_completion_enforce_block,
    "completion_enforce_repair": harness_completion_enforce_repair,
    "cited_but_off_task": harness_cited_but_off_task,
    "judge_manual_review_needed": harness_judge_manual_review_needed,
    "exact_answer_extra_text": harness_exact_answer_extra_text,
    "must_not_include": harness_must_not_include,
    "legacy_string_criterion": harness_legacy_string_criterion,
}


def _config(
    workdir: Path,
    *,
    max_steps: int = 20,
    max_same_operator_streak: int = 3,
    max_no_progress_steps: int = 2,
    answer_reserve_steps: int = 2,
    quality: dict[str, object] | None = None,
) -> AppConfig:
    payload: dict[str, object] = {
        "runtime": {
            "max_steps": max_steps,
            "max_same_operator_streak": max_same_operator_streak,
            "max_no_progress_steps": max_no_progress_steps,
            "answer_reserve_steps": answer_reserve_steps,
        },
        "storage": {"sqlite_path": str(workdir / "memory.db")},
    }
    if quality is not None:
        payload["quality"] = quality
    return AppConfig.model_validate(payload)


def _search_draft(query: str) -> DecisionDraft:
    return DecisionDraft(
        operator=Operator.SEARCH,
        objective="Find task-relevant evidence",
        reason="need external evidence",
        success_criteria=("release identified",),
        params=SearchParams(
            query=query,
            evidence_need="Heuriva release evidence",
            expected_signal="Heuriva release or version details",
            source_scope=SourceScope.WEB,
        ),
        confidence=0.5,
    )
