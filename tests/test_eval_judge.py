from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from heuriva.cli import app
from heuriva.clients.model import ModelChatResult, ModelClientError
from heuriva.core.decision import AnswerParams, DecisionDraft, bind_decision
from heuriva.core.evaluation import DisagreementBucket, JudgeVerdict
from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.eval_judge import JUDGE_PROMPT_VERSION, FreshJudge
from heuriva.evaluation import (
    classify_disagreement,
    compute_verify_gate,
    evaluate_trajectory,
    evaluate_with_judge,
)
from heuriva.storage.sqlite import SCHEMA_VERSION, SQLiteStore


class ScriptedModel:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("unexpected model call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ModelChatResult(content=item, metadata={"attempt_count": 1, "model": "fake"})


def _seed_task(
    store: SQLiteStore,
    *,
    task_id: str = "task-judge",
    completion_verdict: str = "pass",
    citation_validation: str = "passed",
    final_answer: str = "A complete answer about safety and tradeoffs.",
) -> dict[str, Any]:
    state = CognitiveState.new(task_id=task_id, goal="Explain product risks")
    store.create_task_with_trajectory(state, config_snapshot={"model": "fake"})
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANSWER,
            objective="answer",
            reason="done",
            success_criteria=("final",),
            params=AnswerParams(),
            confidence=0.8,
        ),
        state,
    )
    observation = Observation(
        task_id=state.task_id,
        decision_id=decision.id,
        kind=ObservationKind.ANSWER,
        status=ObservationStatus.SUCCESS,
        content=final_answer,
        executor_kind="llm",
        metadata={
            "citation_validation": citation_validation,
            "completion_assessment": {
                "verdict": completion_verdict,
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
        final_answer=final_answer,
    )
    return store.get_trajectory(state.task_id)


def test_default_eval_does_not_call_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["setup"])
    store = SQLiteStore(tmp_path / ".heuriva" / "memory.db")
    _seed_task(store)

    called = {"chat": 0}

    class BoomModelClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
            called["chat"] += 1
            raise AssertionError("default eval must not call model")

        def close(self) -> None:
            return None

    monkeypatch.setattr("heuriva.cli.ModelClient", BoomModelClient)
    result = runner.invoke(app, ["eval", "--json", "task-judge"])

    assert result.exit_code == 0
    assert called["chat"] == 0
    payload = json.loads(result.stdout)
    assert payload["completion_verdict"] == "pass"


def test_fresh_judge_records_provenance_and_persists_without_rewriting_trajectory(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    data = _seed_task(store)
    steps_before = len(data["steps"])
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "verdict": "fail",
                    "reason": "missing tradeoff discussion",
                    "failed_criteria": ["tradeoffs"],
                    "manual_review_needed": False,
                }
            )
        ]
    )
    judge = FreshJudge(
        model_client=model,
        model_name="fake-judge",
        base_url="http://localhost:8765/v1",
        repair_attempts=1,
    )
    report = evaluate_with_judge(data, judge=judge, store=store, persist=True)

    assert report.judge.verdict is JudgeVerdict.FAIL
    assert report.judge.provenance.model == "fake-judge"
    assert report.judge.provenance.prompt_version == JUDGE_PROMPT_VERSION
    assert report.judge.provenance.prompt_hash
    assert report.judge.provenance.task_id == "task-judge"
    assert report.disagreement.bucket is DisagreementBucket.DETERMINISTIC_PASS_JUDGE_FAIL
    assert report.promotion.recommend_enforce is False
    assert report.verify_gate.enter_verify_design is False
    assert report.eval_run_id is not None

    reloaded = store.get_trajectory("task-judge")
    assert len(reloaded["steps"]) == steps_before
    assert reloaded["trajectory"]["final_answer"] == data["trajectory"]["final_answer"]
    runs = store.list_eval_runs("task-judge")
    assert len(runs) == 1
    assert runs[0]["id"] == report.eval_run_id
    assert runs[0]["judge_verdict"] == "fail"
    assert runs[0]["deterministic_verdict"] == "pass"


def test_judge_parse_failure_is_not_pass(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    data = _seed_task(store)
    model = ScriptedModel(["not-json", "still not json"])
    judge = FreshJudge(
        model_client=model,
        model_name="fake-judge",
        base_url="http://localhost:8765/v1",
        repair_attempts=1,
        max_calls=2,
    )
    report = evaluate_with_judge(data, judge=judge, store=store, persist=True)

    assert report.judge.verdict is JudgeVerdict.PARSE_FAILURE
    assert report.disagreement.bucket is DisagreementBucket.JUDGE_UNAVAILABLE
    assert report.judge.provenance.failure_code == "parse_failure"
    assert len(model.calls) == 2


def test_judge_model_error_classifies_failure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    data = _seed_task(store)
    model = ScriptedModel(
        [ModelClientError("timeout", "timed out", retryable=True, attempt_count=2)]
    )
    judge = FreshJudge(
        model_client=model,
        model_name="fake-judge",
        base_url="http://localhost:8765/v1",
    )
    report = evaluate_with_judge(data, judge=judge, store=None, persist=False)

    assert report.judge.verdict is JudgeVerdict.ERROR
    assert report.judge.provenance.failure_code == "timeout"
    assert report.disagreement.bucket is DisagreementBucket.JUDGE_UNAVAILABLE
    assert report.eval_run_id is None


def test_manual_review_needed_is_not_fail() -> None:
    from heuriva.core.evaluation import JudgeAssessment, JudgeProvenance

    judge = JudgeAssessment(
        verdict=JudgeVerdict.INSUFFICIENT_EVIDENCE,
        reason="need human eyes",
        manual_review_needed=True,
        provenance=JudgeProvenance(
            model="fake",
            base_url="http://localhost:8765/v1",
            prompt_version="v0.5.0",
            prompt_hash="abcd",
            timestamp="2026-08-25T00:00:00+00:00",
            task_id="t1",
        ),
    )
    report = classify_disagreement(deterministic_verdict="pass", judge=judge)
    assert report.bucket is DisagreementBucket.MANUAL_REVIEW_NEEDED


def test_verify_gate_requires_two_distinct_leak_tasks() -> None:
    gate = compute_verify_gate(leak_task_ids=("a", "a"))
    assert gate.enter_verify_design is False
    assert gate.distinct_leak_task_count == 1

    gate2 = compute_verify_gate(leak_task_ids=("a", "b"))
    assert gate2.distinct_leak_task_count == 2
    assert gate2.enter_verify_design is False
    assert gate2.conditions_met["at_least_two_distinct_live_leak_tasks"] is True


def test_sqlite_migrates_v1_to_v2_and_keeps_old_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    # Create a v1-looking DB by initializing then downgrading schema_meta.
    store = SQLiteStore(db_path)
    data = _seed_task(store, task_id="legacy-task")
    with __import__("sqlite3").connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS eval_runs")
        conn.execute("UPDATE schema_meta SET version = 1")
        conn.commit()

    assert SQLiteStore.schema_status(db_path) == "outdated"
    migrated = SQLiteStore(db_path)
    assert SQLiteStore.schema_status(db_path) == "current"
    loaded = migrated.get_trajectory("legacy-task")
    assert loaded["trajectory"]["final_answer"] == data["trajectory"]["final_answer"]
    assert SCHEMA_VERSION == 2
    # Writing an eval run must not rewrite trajectory steps.
    steps_before = len(loaded["steps"])
    run_id = migrated.save_eval_run(
        task_id="legacy-task",
        trajectory_id=loaded["trajectory"]["id"],
        case_id=None,
        judge_mode="fresh_judge",
        deterministic_verdict="pass",
        judge_verdict="fail",
        disagreement_bucket="deterministic_pass_judge_fail",
        model="fake",
        prompt_version="v0.5.0",
        prompt_hash="hash",
        provenance={"model": "fake"},
        result={
            "deterministic": {
                **evaluate_trajectory(loaded).to_dict(),
                "citation_validation": "passed",
            }
        },
    )
    assert migrated.get_eval_run(run_id)["id"] == run_id
    assert len(migrated.get_trajectory("legacy-task")["steps"]) == steps_before
    assert migrated.leak_task_ids_from_eval_runs() == ("legacy-task",)


def test_cli_eval_judge_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["setup"])
    store = SQLiteStore(tmp_path / ".heuriva" / "memory.db")
    _seed_task(store)

    class FakeModelClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
            return ModelChatResult(
                content=json.dumps(
                    {
                        "verdict": "pass",
                        "reason": "criteria covered",
                        "failed_criteria": [],
                        "manual_review_needed": False,
                    }
                ),
                metadata={"attempt_count": 1},
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr("heuriva.cli.ModelClient", FakeModelClient)
    result = runner.invoke(app, ["eval", "--judge", "--json", "task-judge"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["deterministic"]["completion_verdict"] == "pass"
    assert payload["judge"]["verdict"] == "pass"
    assert payload["disagreement"]["bucket"] == "agree"
    assert payload["promotion"]["recommend_enforce"] is False
    assert payload["verify_gate"]["enter_verify_design"] is False
    assert payload["eval_run_id"]
    assert store.list_eval_runs("task-judge")
