from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from heuriva.cli import app
from heuriva.runtime.engine import RuntimeInterrupted, RuntimeProgress, RuntimeResult


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout


def test_cli_setup_and_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    setup = runner.invoke(app, ["setup"])
    doctor = runner.invoke(app, ["doctor"])

    assert setup.exit_code == 0
    assert "Created" in setup.stderr
    assert doctor.exit_code == 0
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
        def run(self, task: str, *, trace: bool = False, progress: Any = None) -> RuntimeResult:
            assert task == "demo task"
            assert trace is False
            assert progress is not None
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
        def run(self, task: str, *, trace: bool = False, progress: Any = None) -> RuntimeResult:
            del task, trace, progress
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
