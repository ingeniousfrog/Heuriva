from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from heuriva.clients.search import SearchResult
from heuriva.config import AppConfig, QualityConfig
from heuriva.core.decision import (
    Decision,
    DecisionDraft,
    SearchParams,
    bind_decision,
    normalize_draft_payload,
)
from heuriva.core.evaluation import CompletionVerdict
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, EvidenceItem
from heuriva.core.state_patch import OperationResult, StatePatch
from heuriva.core.task_contract import (
    SearchPolicy,
    SourceScope,
    TaskContract,
)
from heuriva.evaluation import evaluate_trajectory
from heuriva.executors.search import SearchExecutor
from heuriva.runtime.completion_validation import CompletionValidator
from heuriva.runtime.engine import RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import FakeController, FakeExecutor, make_answer_decision


def test_task_contract_is_immutable_and_legacy_state_compatible() -> None:
    contract = TaskContract.model_validate(
        {
            "criteria": (" Mention safety ", "Mention safety", ""),
            "search_policy": "forbidden",
            "evidence_requirement": "required",
            "origin": "user",
        }
    )

    assert len(contract.criteria) == 1
    assert contract.criteria[0].value == "Mention safety"
    assert contract.criteria[0].kind.value == "must_include"
    assert contract.search_policy is SearchPolicy.FORBIDDEN
    with pytest.raises(ValidationError):
        contract.criteria = ()

    legacy = CognitiveState.model_validate(
        {
            "task_id": "task-legacy",
            "revision_index": 0,
            "step_index": 0,
            "goal": "Explain",
        }
    )

    assert legacy.task_contract == TaskContract()


def test_search_params_carry_structured_evidence_intent() -> None:
    draft = DecisionDraft.model_validate(
        normalize_draft_payload(
            {
                "operator": "SEARCH",
                "objective": "inspect local version",
                "reason": "need source scoped evidence",
                "success_criteria": "version identified",
                "params": {
                    "query": "heuriva version",
                    "evidence_need": "the local package version",
                    "expected_signal": "pyproject version value",
                    "source_scope": "local",
                },
                "confidence": 0.5,
            }
        )
    )

    assert isinstance(draft.params, SearchParams)
    assert draft.params.evidence_need == "the local package version"
    assert draft.params.expected_signal == "pyproject version value"
    assert draft.params.source_scope is SourceScope.LOCAL


def test_runtime_search_policy_forbidden_blocks_provider_call(tmp_path: Path) -> None:
    class CountingSearchExecutor:
        calls = 0

        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            self.calls += 1
            return OperationResult(content="provider called")

    search_executor = CountingSearchExecutor()
    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {
                "runtime": {"max_steps": 2, "answer_reserve_steps": 1},
                "storage": {"sqlite_path": str(tmp_path / "memory.db")},
            }
        ),
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
    data = store.get_trajectory(result.task_id)

    assert result.status == "done"
    assert search_executor.calls == 0
    assert data["events"][0]["event_type"] == "search_guard_applied"
    assert data["events"][0]["payload"]["reason"] == "search_forbidden"
    assert data["steps"][0]["observation"]["metadata"]["search_guard"]["reason"] == (
        "search_forbidden"
    )


def test_runtime_duplicate_query_guard_is_deterministic(tmp_path: Path) -> None:
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
    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {
                "runtime": {
                    "max_steps": 4,
                    "max_same_operator_streak": 5,
                    "max_no_progress_steps": 3,
                    "answer_reserve_steps": 1,
                },
                "storage": {"sqlite_path": str(tmp_path / "memory.db")},
            }
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
    data = store.get_trajectory(result.task_id)

    assert result.status == "done"
    assert search_executor.calls == 1
    assert [step["decision"]["operator"] for step in data["steps"]] == [
        "SEARCH",
        "SEARCH",
        "ANSWER",
    ]
    guards = [event for event in data["events"] if event["event_type"] == "search_guard_applied"]
    assert len(guards) == 1
    assert guards[0]["payload"]["reason"] == "duplicate_query"
    assert data["steps"][1]["observation"]["metadata"]["search_guard"]["reason"] == (
        "duplicate_query"
    )


def test_search_executor_keeps_raw_and_rejected_candidates_out_of_state() -> None:
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

    state = CognitiveState.new(task_id="task-1", goal="Explain the Heuriva release")
    decision = bind_decision(_search_draft("heuriva release"), state)
    executor = SearchExecutor(
        search_client=FakeSearchClient(),
        quality_config=QualityConfig.model_validate({"evidence_relevance_mode": "enforce"}),
    )

    result = executor.execute(decision, state)

    assert result.error is None
    assert result.patch is not None
    assert result.patch.evidence_add == ()
    assert result.metadata["raw_candidate_count"] == 1
    assert result.metadata["accepted_evidence_count"] == 0
    assert result.metadata["rejected_candidate_count"] == 1
    assert result.metadata["candidate_assessments"][0]["verdict"] == "irrelevant"


def test_completion_enforce_blocks_done_when_task_contract_is_unmet(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    config = AppConfig.model_validate(
        {
            "quality": {"completion_check_mode": "enforce", "max_completion_repairs": 0},
            "storage": {"sqlite_path": str(tmp_path / "memory.db")},
        }
    )
    engine = RuntimeEngine(
        config=config,
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="A short answer")},
    )

    result = engine.run("Explain safety tradeoffs", criteria=("mention safety",))
    data = store.get_trajectory(result.task_id)

    assert result.status == "failed"
    assert result.final_answer is None
    assert data["trajectory"]["termination_reason"] == "completion_not_met"
    assert data["steps"][0]["observation"]["error"]["code"] == "completion_not_met"
    assert data["steps"][0]["observation"]["metadata"]["completion_assessment"]["verdict"] == (
        "fail"
    )
    assert [event["event_type"] for event in data["events"]] == ["completion_assessed"]


def test_completion_enforce_allows_bounded_repair(tmp_path: Path) -> None:
    class RepairingAnswerExecutor:
        def __init__(self) -> None:
            self.answers = ["A short answer", "A safe answer that mentions safety"]
            self.calls = 0

        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            answer = self.answers[self.calls]
            self.calls += 1
            return OperationResult(content=answer, final_answer=answer)

    store = SQLiteStore(tmp_path / "memory.db")
    config = AppConfig.model_validate(
        {
            "quality": {"completion_check_mode": "enforce", "max_completion_repairs": 1},
            "storage": {"sqlite_path": str(tmp_path / "memory.db")},
        }
    )
    executor = RepairingAnswerExecutor()
    engine = RuntimeEngine(
        config=config,
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
    data = store.get_trajectory(result.task_id)

    assert result.status == "done"
    assert result.final_answer == "A safe answer that mentions safety"
    assert executor.calls == 2
    assert data["steps"][0]["observation"]["error"]["code"] == ("completion_validation_error")
    assert data["steps"][0]["observation"]["error"]["retryable"] is True
    assert data["steps"][1]["observation"]["metadata"]["completion_assessment"]["verdict"] == "pass"


def test_completion_assessment_matches_common_chinese_equivalents() -> None:
    config = QualityConfig.model_validate({"completion_check_mode": "observe"})
    validator = CompletionValidator(config)
    state = CognitiveState.new(
        task_id="task-1",
        goal="Evaluate moderation policy",
        task_contract=TaskContract.from_user(
            criteria=("mention safety", "mention tradeoffs"),
        ),
    )

    assessment = validator.assess(
        answer=(
            "结论：可以有条件推进。核心安全问题是避免对未成年用户造成伤害，"
            "主要代价是转化率下降和审核摩擦增加，权衡点在增长与风险控制之间。"
        ),
        state=state,
    )

    assert assessment is not None
    assert assessment.verdict is CompletionVerdict.PASS


def test_evaluate_trajectory_summarizes_v03_quality_fields(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {
                "runtime": {"max_steps": 3, "answer_reserve_steps": 1},
                "quality": {"completion_check_mode": "observe"},
                "storage": {"sqlite_path": str(tmp_path / "memory.db")},
            }
        ),
        store=store,
        controller=FakeController(
            [_search_draft("heuriva release"), make_answer_decision("Answer")]
        ),
        executors={
            Operator.SEARCH: FakeExecutor("search", evidence_url="https://example.com/heuriva"),
            Operator.ANSWER: FakeExecutor("final", final_answer="Heuriva release is noted."),
        },
    )

    result = engine.run("Explain the Heuriva release", criteria=("release",))
    report = evaluate_trajectory(store.get_trajectory(result.task_id))

    assert report.task_id == result.task_id
    assert report.search_steps == 1
    assert report.accepted_evidence_count >= 1
    assert report.completion_verdict in {"pass", "not_assessed"}
    assert report.evidence_level in {"deterministic", "stored_model_assessment"}


def test_evaluate_trajectory_prefers_v03_metadata_over_delta_count() -> None:
    report = evaluate_trajectory(
        {
            "trajectory": {"task_id": "task-1", "status": "done"},
            "steps": [
                {
                    "decision": {"operator": "SEARCH"},
                    "observation": {
                        "metadata": {
                            "raw_candidate_count": 5,
                            "accepted_evidence_count": 5,
                            "rejected_candidate_count": 0,
                        }
                    },
                    "state_delta": {"added_evidence_count": 5},
                }
            ],
            "events": [],
        }
    )

    assert report.raw_candidate_count == 5
    assert report.accepted_evidence_count == 5
    assert report.rejected_candidate_count == 0


def test_v03_fixtures_are_labeled_by_evidence_grade() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "v03_eval_tasks.yaml"
    loaded = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))

    assert len(loaded["cases"]) >= 6
    assert {case["evidence_level"] for case in loaded["cases"]} == {
        "synthetic",
        "fake_integration",
        "stored_live",
        "fresh_live",
    }
    assert all(case["expected_quality_signal"] for case in loaded["cases"])


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
