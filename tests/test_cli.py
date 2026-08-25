from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from heuriva import __version__
from heuriva.cli import app
from heuriva.core.common import utc_now
from heuriva.core.decision import AnswerParams, DecisionDraft, bind_decision
from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.runtime.engine import RuntimeInterrupted, RuntimeProgress, RuntimeResult
from heuriva.storage.sqlite import SQLiteStore


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "serve" in result.stdout


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"Heuriva {__version__}"


def test_cli_setup_and_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    setup = runner.invoke(app, ["setup"])
    doctor = runner.invoke(app, ["doctor"])

    assert setup.exit_code == 0
    assert "Created" in setup.stderr
    assert doctor.exit_code == 0
    assert f"Version: {__version__}" in doctor.stderr
    assert "SQLite schema" in doctor.stderr


def test_cli_doctor_probe_timeout_overrides_quick_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    captured: list[dict[str, Any]] = []

    class FakeModelClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

        def models_probe(self) -> tuple[bool, str]:
            return True, "ok"

        def chat(self, messages: list[dict[str, str]]) -> object:
            assert messages == [{"role": "user", "content": "Reply with ok."}]
            return type("ChatResponse", (), {"content": "ok"})()

        def close(self) -> None:
            return None

    monkeypatch.setattr("heuriva.cli.ModelClient", FakeModelClient)
    runner = CliRunner()
    runner.invoke(app, ["setup"])

    default_probe = runner.invoke(app, ["doctor", "--probe"])
    custom_probe = runner.invoke(app, ["doctor", "--probe", "--probe-timeout", "30"])

    assert default_probe.exit_code == 0
    assert custom_probe.exit_code == 0
    assert captured[0]["read_timeout_seconds"] == 2.0
    assert captured[1]["read_timeout_seconds"] == 30.0
    assert "Probe timeout: 30s" in custom_probe.stderr


def test_cli_run_json_keeps_stdout_machine_readable_and_streams_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        def run(
            self,
            task: str,
            *,
            trace: bool = False,
            progress: Any = None,
            criteria: tuple[str, ...] = (),
            search_policy: object = "auto",
        ) -> RuntimeResult:
            assert task == "demo task"
            assert trace is False
            assert progress is not None
            assert criteria == ()
            assert str(search_policy) == "auto"
            progress(
                RuntimeProgress(
                    task_id="task-123456",
                    step_index=0,
                    stage="task_started",
                    message="started task; final JSON will be printed on stdout",
                    elapsed_seconds=0.0,
                )
            )
            return RuntimeResult(
                task_id="task-123456",
                status="done",
                final_answer="done",
                steps=[],
                trace_lines=[],
            )

    monkeypatch.setattr("heuriva.cli._build_engine", lambda: FakeEngine())

    result = CliRunner().invoke(app, ["run", "--json", "demo task"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "task_id": "task-123456",
        "status": "done",
        "final_answer": "done",
    }
    assert "started task; final JSON will be printed on stdout" in result.stderr
    assert result.stdout.strip().startswith("{")


def test_cli_run_interrupt_prints_full_task_id_and_show_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        def run(
            self,
            task: str,
            *,
            trace: bool = False,
            progress: Any = None,
            criteria: tuple[str, ...] = (),
            search_policy: object = "auto",
        ) -> RuntimeResult:
            del task, trace, progress, criteria, search_policy
            raise RuntimeInterrupted("task-123456789")

    monkeypatch.setattr("heuriva.cli._build_engine", lambda: FakeEngine())

    result = CliRunner().invoke(app, ["run", "--json", "long task"])

    assert result.exit_code == 130
    assert result.stdout == ""
    assert "Interrupted. task_id=task-123456789" in result.stderr
    assert "heuriva show --trace task-123456789" in result.stderr


def test_cli_show_missing_task_returns_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    CliRunner().invoke(app, ["setup"])

    result = CliRunner().invoke(app, ["show", "missing"])

    assert result.exit_code == 4
    assert "not found" in result.stderr


def test_cli_eval_json_reads_saved_trajectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["setup"])
    store = SQLiteStore(tmp_path / ".heuriva" / "memory.db")
    state = CognitiveState.new(task_id="task-eval", goal="Explain")
    store.create_task_with_trajectory(state, config_snapshot={})
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
        content="Final",
        executor_kind="llm",
        metadata={
            "completion_assessment": {
                "verdict": "pass",
                "failed_criteria": [],
            }
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
        final_answer="Final",
    )

    result = runner.invoke(app, ["eval", "--json", state.task_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task_id"] == "task-eval"
    assert payload["completion_verdict"] == "pass"
    assert result.stderr == ""


def test_cli_doctor_reports_effective_runtime_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["setup"])
    db_path = tmp_path / ".heuriva" / "memory.db"
    store = SQLiteStore(db_path)
    state = CognitiveState.new(task_id="task-stale", goal="unfinished")
    store.create_task_with_trajectory(state, config_snapshot={})
    stale_time = (utc_now() - timedelta(seconds=1200)).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (stale_time, state.task_id),
        )
        conn.commit()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "LLM timeouts: connect=5s read=180s retries=1" in result.stderr
    assert "Search timeout: 15s" in result.stderr
    assert "Stale running tasks: 1" in result.stderr
    assert "Oldest stale task: task-stale" in result.stderr
