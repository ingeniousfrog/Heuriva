from __future__ import annotations

from pathlib import Path

import pytest

from heuriva.config import AppConfig
from heuriva.core.decision import Decision
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.state_patch import OperationResult
from heuriva.runtime.engine import ResumeRejected, RuntimeEngine, RuntimeInterrupted
from heuriva.runtime.resume import assess_resume_eligibility, config_snapshot_warnings
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import (
    FakeController,
    FakeExecutor,
    make_analyze_decision,
    make_answer_decision,
)


def test_assess_resume_rejects_done_without_force() -> None:
    result = assess_resume_eligibility(
        task_id="t1",
        task_status="done",
        step_count=3,
        force=False,
    )
    assert result.eligible is False
    assert result.reason == "already_done"


def test_assess_resume_allows_done_with_force() -> None:
    result = assess_resume_eligibility(
        task_id="t1",
        task_status="done",
        step_count=3,
        force=True,
    )
    assert result.eligible is True
    assert result.reason == "forced_done"
    assert "force_resume_done_task" in result.warnings


def test_assess_resume_allows_interrupted() -> None:
    result = assess_resume_eligibility(
        task_id="t1",
        task_status="interrupted",
        step_count=1,
    )
    assert result.eligible is True
    assert result.reason == "ok"


def test_config_snapshot_warnings_detect_model_drift() -> None:
    warnings = config_snapshot_warnings(
        {"llm": {"base_url": "http://localhost:8765/v1", "model": "auto"}},
        {"llm": {"base_url": "http://localhost:8765/v1", "model": "other"}},
    )
    assert warnings == ("config_drift:llm.model",)


def test_resume_after_interrupt_appends_without_rewriting_history(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    config = AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}})

    class InterruptOnAnswer:
        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            raise KeyboardInterrupt

    engine = RuntimeEngine(
        config=config,
        store=store,
        controller=FakeController(
            [make_analyze_decision("Understand"), make_answer_decision("Answer")]
        ),
        executors={
            Operator.ANALYZE: FakeExecutor("analysis", known="Need more work"),
            Operator.ANSWER: InterruptOnAnswer(),
        },
    )
    with pytest.raises(RuntimeInterrupted) as exc_info:
        engine.run("Explain Heuriva", trace=False)
    task_id = exc_info.value.task_id
    before = store.trajectory_step_fingerprints(task_id)
    assert len(before) == 1

    resumed = RuntimeEngine(
        config=config,
        store=store,
        controller=FakeController([make_answer_decision("Finish")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="Resumed answer")},
    ).resume(task_id, trace=False)

    assert resumed.status == "done"
    assert resumed.resumed is True
    assert resumed.final_answer == "Resumed answer"
    after = store.trajectory_step_fingerprints(task_id)
    assert after[:1] == before
    assert len(after) == 2
    data = store.get_trajectory(task_id)
    assert data["trajectory"]["status"] == "done"
    assert any(event["event_type"] == "task_resumed" for event in data["events"])


def test_resume_rejects_done_task(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}}),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="Done once")},
    )
    result = engine.run("Explain", trace=False)
    assert result.status == "done"
    with pytest.raises(ResumeRejected) as exc_info:
        engine.resume(result.task_id)
    assert exc_info.value.eligibility.reason == "already_done"


def test_resume_missing_task_raises_key_error(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}}),
        store=store,
        controller=FakeController([]),
        executors={},
    )
    with pytest.raises(KeyError):
        engine.resume("missing-task-id")
