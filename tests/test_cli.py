from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from heuriva.cli import app


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


def test_cli_show_missing_task_returns_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    CliRunner().invoke(app, ["setup"])

    result = CliRunner().invoke(app, ["show", "missing"])

    assert result.exit_code == 4
    assert "not found" in result.stderr
