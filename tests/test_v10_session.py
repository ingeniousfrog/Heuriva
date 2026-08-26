"""v1.0 Local Session UI — service layer, busy gate, and HTTP API tests."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from typer.testing import CliRunner

from heuriva import __version__
from heuriva.cli import app
from heuriva.config import AppConfig, QualityConfig
from heuriva.core.decision import DecisionDraft
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.task_contract import TaskContract
from heuriva.runtime.engine import ResumeRejected, RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import (
    FakeController,
    FakeExecutor,
    make_answer_decision,
)
from heuriva.web.html import render_session_home, render_task_detail
from heuriva.web.queries import TrajectoryBrowser, trajectory_steps_fingerprint
from heuriva.web.server import serve_browser
from heuriva.web.session import SessionBusy, SessionService


def _config(tmp_path: Path, **runtime: object) -> AppConfig:
    payload: dict[str, object] = {
        "runtime": {"max_steps": 2, "answer_reserve_steps": 1, **runtime},
        "quality": {"completion_check_mode": "observe"},
        "storage": {"sqlite_path": str(tmp_path / "memory.db")},
    }
    return AppConfig.model_validate(payload)


def _engine(
    store: SQLiteStore,
    tmp_path: Path,
    *,
    drafts: list[DecisionDraft] | None = None,
) -> RuntimeEngine:
    controller = FakeController(drafts or [make_answer_decision("Answer")])
    return RuntimeEngine(
        config=_config(tmp_path),
        store=store,
        controller=controller,
        executors={Operator.ANSWER: FakeExecutor("final", final_answer="OK")},
    )


def test_session_run_and_busy_gate(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    slow = threading.Event()

    class SlowExecutor(FakeExecutor):
        def execute(self, decision, state):  # type: ignore[no-untyped-def]
            slow.wait(timeout=2.0)
            return super().execute(decision, state)

    def factory() -> RuntimeEngine:
        return RuntimeEngine(
            config=_config(tmp_path),
            store=store,
            controller=FakeController([make_answer_decision("Answer")]),
            executors={Operator.ANSWER: SlowExecutor("final", final_answer="OK")},
        )

    session = SessionService(store=store, engine_factory=factory)
    accepted = session.start_run("slow task")
    assert accepted["accepted"] is True
    assert accepted["task_id"]
    assert session.snapshot().busy is True

    raised = False
    try:
        session.start_run("second")
    except SessionBusy:
        raised = True
    assert raised

    slow.set()
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    snap = session.snapshot()
    assert snap.busy is False
    assert snap.result_status == "done"
    assert snap.task_id == accepted["task_id"]


def test_session_interrupt_like_ctrl_c(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    entered = threading.Event()
    session_box: dict[str, SessionService] = {}

    class BlockingExecutor(FakeExecutor):
        def execute(self, decision, state):  # type: ignore[no-untyped-def]
            del decision, state
            entered.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                session = session_box.get("session")
                if session is not None and session._interrupt_requested.is_set():
                    raise KeyboardInterrupt
                time.sleep(0.05)
            raise AssertionError("interrupt was not requested in time")

    def factory() -> RuntimeEngine:
        return RuntimeEngine(
            config=_config(tmp_path),
            store=store,
            controller=FakeController([make_answer_decision("Answer")]),
            executors={Operator.ANSWER: BlockingExecutor("final", final_answer="OK")},
        )

    session = SessionService(store=store, engine_factory=factory)
    session_box["session"] = session
    accepted = session.start_run("interrupt me")
    assert accepted["busy"] is True
    assert entered.wait(timeout=2.0)
    result = session.request_interrupt()
    assert result["accepted"] is True
    deadline = time.time() + 3
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    snap = session.snapshot()
    assert snap.busy is False
    assert snap.result_status == "interrupted" or snap.error_code == "interrupted"
    assert any(entry.get("stage") == "interrupt_requested" for entry in snap.log)

    with pytest.raises(ValueError, match="nothing to interrupt"):
        session.request_interrupt()


def test_session_resume_rejects_done_without_force(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")

    def factory() -> RuntimeEngine:
        return _engine(store, tmp_path)

    session = SessionService(store=store, engine_factory=factory)
    task_id = session.start_run("done task")["task_id"]
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    assert session.snapshot().result_status == "done"

    eligibility = session.resume_eligibility(task_id)
    assert eligibility.eligible is False
    assert eligibility.reason == "already_done"

    raised = False
    try:
        session.start_resume(task_id, force=False)
    except ResumeRejected as exc:
        raised = True
        assert exc.eligibility.reason == "already_done"
    assert raised

    before = trajectory_steps_fingerprint(store.get_trajectory(task_id))
    accepted = session.start_resume(task_id, force=True)
    assert accepted["accepted"] is True
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    after = store.get_trajectory(task_id)
    assert trajectory_steps_fingerprint(after)[: len(before)] == before
    assert len(after["steps"]) >= len(before)


def test_http_session_api_run_status_and_busy(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    gate = threading.Event()

    class SlowExecutor(FakeExecutor):
        def execute(self, decision, state):  # type: ignore[no-untyped-def]
            gate.wait(timeout=2.0)
            return super().execute(decision, state)

    def factory() -> RuntimeEngine:
        return RuntimeEngine(
            config=_config(tmp_path),
            store=store,
            controller=FakeController([make_answer_decision("Answer")]),
            executors={Operator.ANSWER: SlowExecutor("final", final_answer="OK")},
        )

    browser = TrajectoryBrowser(store)
    session = SessionService(store=store, engine_factory=factory, browser=browser)
    server = serve_browser(
        browser=browser,
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "memory.db",
        session=session,
    )
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://127.0.0.1:{port}/api/run",
            data=json.dumps({"goal": "from api"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as response:
            assert response.status == 202
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["task_id"]
        task_id = payload["task_id"]

        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["busy"] is True
        assert status["task_id"] == task_id

        busy_req = Request(
            f"http://127.0.0.1:{port}/api/run",
            data=json.dumps({"goal": "blocked"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(busy_req, timeout=5)
            raise AssertionError("expected 409")
        except HTTPError as exc:
            assert exc.code == 409
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "session_busy"

        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as response:
                status = json.loads(response.read().decode("utf-8"))
            if not status["busy"]:
                break
            time.sleep(0.05)
        assert status["busy"] is False
        assert status["result_status"] == "done"

        with urlopen(f"http://127.0.0.1:{port}/?format=json", timeout=5) as response:
            listing = json.loads(response.read().decode("utf-8"))
        assert listing["session_enabled"] is True
        assert listing["read_only"] is False
        assert listing["tasks"][0]["task_id"] == task_id
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_resume_done_rejected(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")

    def factory() -> RuntimeEngine:
        return _engine(store, tmp_path)

    browser = TrajectoryBrowser(store)
    session = SessionService(store=store, engine_factory=factory, browser=browser)
    task_id = session.start_run("done via api")["task_id"]
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)

    server = serve_browser(
        browser=browser,
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "memory.db",
        session=session,
    )
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://127.0.0.1:{port}/api/resume",
            data=json.dumps({"task_id": task_id}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(req, timeout=5)
            raise AssertionError("expected 409")
        except HTTPError as exc:
            assert exc.code == 409
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "resume_rejected"
            assert body["eligibility"]["reason"] == "already_done"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_session_html_has_brand_and_composer(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    session = SessionService(
        store=store,
        engine_factory=lambda: _engine(store, tmp_path),
    )
    task_id = session.start_run("branded page")["task_id"]
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    items = TrajectoryBrowser(store).list_tasks()
    home = render_session_home(
        items,
        db_path="~/.heuriva/memory.db",
        session_enabled=True,
    )
    assert "Heuriva" in home
    assert "session-goal" in home
    assert "activity-feed" in home
    assert "activity-interrupt" in home
    assert "recent-drawer" in home
    assert "settings-base-url" in home
    assert "settings-modal" in home
    assert "settings-open" in home
    assert "session-form" in home
    assert "is-running" in home or "composer-card" in home
    assert task_id[:8] in home
    assert "~/.heuriva/memory.db" in home

    detail = render_task_detail(
        TrajectoryBrowser(store).get_task(task_id),
        db_path="~/.heuriva/memory.db",
        session_enabled=True,
        resume_eligibility=session.resume_eligibility(task_id).to_dict(),
    )
    assert "Force resume" in detail or "Resume" in detail
    assert 'data-i18n="steps"' in detail or ">Steps<" in detail
    assert 'id="activity-feed"' not in detail
    assert 'id="activity-interrupt"' not in detail
    assert 'data-resume-btn' in detail


def test_serve_help_mentions_session() -> None:
    result = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}).invoke(
        app, ["serve", "--help"]
    )
    assert result.exit_code == 0
    help_text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
    assert "Session" in help_text or "session" in help_text.lower()
    assert "--read-only" in help_text


def test_version_and_quality_defaults_v10() -> None:
    assert __version__ == "1.0.1"
    assert QualityConfig().completion_check_mode.value == "observe"
    state = CognitiveState.new(
        task_id="session-version",
        goal="x",
        task_contract=TaskContract.from_user(),
    )
    assert state.task_id == "session-version"


def test_progress_log_in_status(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    session = SessionService(
        store=store,
        engine_factory=lambda: _engine(store, tmp_path),
    )
    session.start_run("log me")
    deadline = time.time() + 5
    while session.snapshot().busy and time.time() < deadline:
        time.sleep(0.05)
    snap = session.snapshot().to_dict()
    assert snap["log"]
    assert any(entry.get("stage") == "accepted" for entry in snap["log"])
    assert any(entry.get("stage") == "completed" for entry in snap["log"])


def test_http_settings_and_display_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from heuriva.config import load_config, setup_config
    from heuriva.web.display import display_storage_path

    assert display_storage_path(Path("~/.heuriva/memory.db")) == "~/.heuriva/memory.db"

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    setup_config(home=home)
    db = home / ".heuriva" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(db)
    session = SessionService(
        store=store,
        engine_factory=lambda: _engine(store, tmp_path),
    )
    browser = TrajectoryBrowser(store)
    server = serve_browser(
        browser=browser,
        host="127.0.0.1",
        port=0,
        db_path=db,
        session=session,
    )
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/settings", timeout=5) as response:
            settings = json.loads(response.read().decode("utf-8"))
        assert "db_path" in settings
        assert settings["db_path"].startswith("~/")
        assert "base_url" in settings

        req = Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps(
                {"base_url": "http://127.0.0.1:9999/v1", "model": "test-model"}
            ).encode(),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as response:
            updated = json.loads(response.read().decode("utf-8"))
        assert updated["base_url"] == "http://127.0.0.1:9999/v1"
        assert updated["model"] == "test-model"
        cfg = load_config(home=home)
        assert cfg.llm.base_url == "http://127.0.0.1:9999/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
