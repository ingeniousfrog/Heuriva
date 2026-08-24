from __future__ import annotations

from pathlib import Path

import pytest

from heuriva.clients.model import ModelChatResult, ModelClientError
from heuriva.config import AppConfig
from heuriva.controller.llm_controller import LLMController
from heuriva.core.decision import Decision
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.state_patch import OperationResult
from heuriva.runtime.engine import RuntimeEngine, RuntimeInterrupted, RuntimeProgress
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import (
    FakeController,
    FakeExecutor,
    make_analyze_decision,
    make_answer_decision,
    make_search_decision,
)


def test_runtime_runs_dynamic_analyze_search_answer_path(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    controller = FakeController(
        [
            make_analyze_decision("Understand"),
            make_search_decision("adaptive cognitive runtime"),
            make_answer_decision("Answer"),
        ]
    )
    engine = RuntimeEngine(
        config=AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}}),
        store=store,
        controller=controller,
        executors={
            Operator.ANALYZE: FakeExecutor("analysis", known="The task needs evidence"),
            Operator.SEARCH: FakeExecutor("search", evidence_url="https://example.com/source"),
            Operator.ANSWER: FakeExecutor("final", final_answer="Final answer"),
        },
    )

    result = engine.run("Explain Heuriva", trace=False)

    assert result.status == "done"
    assert result.final_answer == "Final answer"
    assert [step.decision.operator for step in result.steps] == [
        Operator.ANALYZE,
        Operator.SEARCH,
        Operator.ANSWER,
    ]
    assert len(store.get_trajectory(result.task_id)["steps"]) == 3


def test_runtime_can_finish_without_search_when_controller_chooses_so(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    controller = FakeController(
        [make_analyze_decision("Understand"), make_answer_decision("Answer")]
    )
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {
                "storage": {"sqlite_path": str(tmp_path / "memory.db")},
                "tools": {"search": {"enabled": False}},
            }
        ),
        store=store,
        controller=controller,
        executors={
            Operator.ANALYZE: FakeExecutor("analysis", known="The task is direct"),
            Operator.ANSWER: FakeExecutor("final", final_answer="Direct answer"),
        },
    )

    result = engine.run("Explain", trace=False)

    assert result.status == "done"
    assert [step.decision.operator for step in result.steps] == [Operator.ANALYZE, Operator.ANSWER]


def test_runtime_emits_live_progress_events(tmp_path: Path) -> None:
    progress_events: list[RuntimeProgress] = []
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {"runtime": {"max_steps": 1}, "storage": {"sqlite_path": str(tmp_path / "memory.db")}}
        ),
        store=SQLiteStore(tmp_path / "memory.db"),
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="Final answer")},
    )

    result = engine.run("Explain", progress=progress_events.append)

    assert result.status == "done"
    assert [event.stage for event in progress_events] == [
        "task_started",
        "controller_selecting",
        "operator_selected",
        "executor_running",
        "step_committed",
        "task_finished",
    ]
    assert progress_events[2].operator == "ANSWER"
    assert "selected ANSWER" in progress_events[2].message
    assert "use `heuriva show --trace" in progress_events[-1].message


def test_runtime_last_step_only_exposes_answer(tmp_path: Path) -> None:
    controller = FakeController([make_analyze_decision("A"), make_search_decision("blocked")])
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {"runtime": {"max_steps": 2}, "storage": {"sqlite_path": str(tmp_path / "memory.db")}}
        ),
        store=SQLiteStore(tmp_path / "memory.db"),
        controller=controller,
        executors={
            Operator.ANALYZE: FakeExecutor("analysis"),
            Operator.SEARCH: FakeExecutor("search"),
        },
    )

    result = engine.run("Explain", trace=False)

    assert result.status == "failed"
    assert controller.available_history[-1] == (Operator.ANSWER,)


def test_runtime_empty_final_answer_reaches_max_steps(tmp_path: Path) -> None:
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {"runtime": {"max_steps": 1}, "storage": {"sqlite_path": str(tmp_path / "memory.db")}}
        ),
        store=SQLiteStore(tmp_path / "memory.db"),
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("empty", final_answer=" ")},
    )

    result = engine.run("Explain", trace=False)

    assert result.status == "max_steps_reached"
    assert result.final_answer is None


def test_runtime_interrupt_finalizes_and_exposes_task_id(tmp_path: Path) -> None:
    class InterruptingExecutor:
        def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
            del decision, state
            raise KeyboardInterrupt

    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}}),
        store=store,
        controller=FakeController([make_analyze_decision("Wait")]),
        executors={Operator.ANALYZE: InterruptingExecutor()},
    )

    with pytest.raises(RuntimeInterrupted) as exc_info:
        engine.run("Explain")

    data = store.get_trajectory(exc_info.value.task_id)
    assert data["trajectory"]["status"] == "interrupted"
    assert data["trajectory"]["termination_reason"] == "keyboard_interrupt"
    assert data["steps"] == []
    assert [event["event_type"] for event in data["events"]] == ["interrupted"]


def test_runtime_preserves_model_connection_error_in_failure_event(tmp_path: Path) -> None:
    progress_events: list[RuntimeProgress] = []

    class FailingModelClient:
        def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
            del messages
            raise ModelClientError(
                "connection_error",
                "could not connect to model endpoint",
            )

    store = SQLiteStore(tmp_path / "memory.db")
    engine = RuntimeEngine(
        config=AppConfig.model_validate({"storage": {"sqlite_path": str(tmp_path / "memory.db")}}),
        store=store,
        controller=LLMController(model_client=FailingModelClient()),
        executors={},
    )

    result = engine.run("Explain", progress=progress_events.append)
    data = store.get_trajectory(result.task_id)

    assert result.status == "failed"
    assert data["trajectory"]["termination_reason"] == "runtime_error"
    assert data["steps"] == []
    assert [event["event_type"] for event in data["events"]] == ["runtime_error"]
    assert data["events"][0]["payload"] == {
        "error": "ModelClientError",
        "message": "could not connect to model endpoint",
        "code": "connection_error",
        "retryable": False,
    }
    runtime_progress = [event for event in progress_events if event.stage == "runtime_error"]
    assert len(runtime_progress) == 1
    assert "connection_error" in runtime_progress[0].message
    assert "could not connect to model endpoint" in runtime_progress[0].message
