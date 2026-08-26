from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from typer.testing import CliRunner

from heuriva import __version__
from heuriva.cli import app
from heuriva.config import AppConfig, QualityConfig
from heuriva.core.evaluation import CompletionVerdict
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.task_contract import TaskContract
from heuriva.runtime.engine import RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import FakeController, FakeExecutor, make_answer_decision
from heuriva.web.html import render_task_detail, render_task_list
from heuriva.web.queries import (
    TrajectoryBrowser,
    extract_completion_assessment,
    trajectory_steps_fingerprint,
)
from heuriva.web.server import is_loopback_host, serve_browser


def _seed_task(store: SQLiteStore, tmp_path: Path, *, goal: str = "Inspect me") -> str:
    chinese_answer = (
        "结论：有条件做。主风险是不可逆伤害与未成年人保护。主要代价是转化下降；权衡上偏保守。"
    )
    engine = RuntimeEngine(
        config=AppConfig.model_validate(
            {
                "runtime": {"max_steps": 2, "answer_reserve_steps": 1},
                "quality": {"completion_check_mode": "observe"},
                "storage": {"sqlite_path": str(tmp_path / "memory.db")},
            }
        ),
        store=store,
        controller=FakeController([make_answer_decision("Answer")]),
        executors={Operator.ANSWER: FakeExecutor("final", final_answer=chinese_answer)},
    )
    result = engine.run(
        goal,
        criteria=("mention safety", "mention tradeoffs"),
    )
    return result.task_id


def test_list_tasks_and_detail_dto(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    task_id = _seed_task(store, tmp_path)
    browser = TrajectoryBrowser(store)

    items = browser.list_tasks()
    assert len(items) == 1
    assert items[0].task_id == task_id
    assert items[0].status == "done"
    assert items[0].step_count == 1
    assert "Inspect" in items[0].goal_summary

    detail = browser.get_task(task_id)
    assert detail.task_id == task_id
    assert detail.citation_validation in {"passed", "not_assessed", "failed"}
    assert detail.completion_verdict == "pass"
    assert detail.completion_assessment is not None
    kinds = {
        str(item.get("kind"))
        for item in detail.completion_assessment.get("criterion_results") or ()
    }
    assert "must_include" in kinds
    assert detail.task_contract is not None
    assert detail.task_contract.get("criteria")
    assert len(detail.steps) == 1
    assert detail.steps[0].operator == "ANSWER"


def test_browser_read_path_does_not_mutate_steps(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    task_id = _seed_task(store, tmp_path)
    before = trajectory_steps_fingerprint(store.get_trajectory(task_id))
    steps_before = store.count_trajectory_steps(task_id)

    browser = TrajectoryBrowser(store)
    browser.list_tasks()
    detail = browser.get_task(task_id)
    assert detail.task_id == task_id

    after = trajectory_steps_fingerprint(store.get_trajectory(task_id))
    assert after == before
    assert store.count_trajectory_steps(task_id) == steps_before


def test_html_renders_quality_signals(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    task_id = _seed_task(store, tmp_path)
    browser = TrajectoryBrowser(store)
    detail = browser.get_task(task_id)
    list_html = render_task_list(browser.list_tasks(), db_path=str(tmp_path / "memory.db"))
    detail_html = render_task_detail(detail, db_path=str(tmp_path / "memory.db"))

    assert task_id[:8] in list_html
    assert "Contract" in detail_html or "quality" in detail_html.lower()
    assert "must_include" in detail_html or "criteria" in detail_html


def test_http_server_json_list_and_detail_are_read_only(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    task_id = _seed_task(store, tmp_path)
    before = trajectory_steps_fingerprint(store.get_trajectory(task_id))
    browser = TrajectoryBrowser(store)
    server = serve_browser(
        browser=browser,
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "memory.db",
    )
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/?format=json", timeout=5) as response:
            listing = json.loads(response.read().decode("utf-8"))
        assert listing["read_only"] is True
        assert listing["tasks"][0]["task_id"] == task_id

        with urlopen(f"http://127.0.0.1:{port}/tasks/{task_id}?format=json", timeout=5) as response:
            detail = json.loads(response.read().decode("utf-8"))
        assert detail["task_id"] == task_id
        assert detail["completion_verdict"] == "pass"
        assert detail["completion_assessment"]["verdict"] == "pass"

        req = Request(
            f"http://127.0.0.1:{port}/tasks/{task_id}",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(req, timeout=5)
            raised = False
        except HTTPError as exc:
            raised = True
            assert exc.code == 405
        assert raised
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert trajectory_steps_fingerprint(store.get_trajectory(task_id)) == before


def test_serve_help_and_loopback_helper() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--db" in result.stdout
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")


def test_extract_completion_assessment_keeps_kind() -> None:
    data = {
        "steps": [
            {
                "observation": {
                    "metadata": {
                        "completion_assessment": {
                            "verdict": "pass",
                            "criterion_results": [
                                {
                                    "criterion": "mention safety",
                                    "kind": "must_include",
                                    "verdict": "pass",
                                    "reason": "matched",
                                }
                            ],
                        }
                    }
                }
            }
        ]
    }
    assessment = extract_completion_assessment(data)
    assert assessment is not None
    assert assessment["criterion_results"][0]["kind"] == "must_include"
    assert CompletionVerdict(assessment["verdict"]) is CompletionVerdict.PASS


def test_version_and_quality_defaults() -> None:
    assert __version__ == "1.0.0"
    state = CognitiveState.new(
        task_id="browser-version",
        goal="x",
        task_contract=TaskContract.from_user(criteria=("mention safety",)),
    )
    assert state.task_id == "browser-version"
    assert QualityConfig().completion_check_mode.value == "observe"
