from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from heuriva.cli import app
from heuriva.core.evaluation import EvalCorpus, EvalCorpusCase, EvidenceLevel
from heuriva.core.state import CognitiveState
from heuriva.evaluation import load_eval_corpus, run_eval_suite
from heuriva.storage.sqlite import SQLiteStore

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "v04_eval_corpus.yaml"


def test_v04_corpus_schema_rejects_unknown_fields() -> None:
    payload = yaml.safe_load(FIXTURE_CORPUS.read_text(encoding="utf-8"))
    corpus = EvalCorpus.model_validate(payload)

    assert corpus.version == "0.4"
    assert {case.evidence_level for case in corpus.cases} == {
        EvidenceLevel.SYNTHETIC,
        EvidenceLevel.FAKE_INTEGRATION,
        EvidenceLevel.STORED_LIVE,
        EvidenceLevel.FRESH_LIVE,
    }
    with pytest.raises(ValidationError):
        EvalCorpusCase.model_validate(
            {
                "id": "bad",
                "prompt": "x",
                "evidence_level": "synthetic",
                "expected_quality_signal": "signal",
                "unknown_field": True,
            }
        )


def test_eval_suite_runs_deterministic_and_fake_cases_offline(tmp_path: Path) -> None:
    report = run_eval_suite(
        corpus_path=FIXTURE_CORPUS,
        sqlite_path=tmp_path / "missing.db",
        include_fresh_live=False,
        workdir=tmp_path / "suite",
    )

    by_id = {result.case_id: result for result in report.case_results}
    assert by_id["forbidden_search_guard"].status == "pass"
    assert by_id["duplicate_query_guard"].status == "pass"
    assert by_id["irrelevant_candidate_rejected"].status == "pass"
    assert by_id["completion_enforce_block"].status == "pass"
    assert by_id["completion_enforce_repair"].status == "pass"
    assert by_id["cited_but_off_task"].status == "pass"
    assert by_id["stored_live_relevance_readback"].status == "missing"
    assert by_id["fresh_live_opt_in"].status == "skipped"
    assert report.totals_by_status.get("fail", 0) == 0
    assert report.promotion is not None
    assert report.promotion.recommend_enforce is False
    assert report.disclaimer
    assert all(not result.is_product_proof for result in report.case_results)


def test_eval_suite_summarizes_stored_live_without_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    store = SQLiteStore(db_path)
    state = CognitiveState.new(task_id="task-live-1", goal="Explain")
    store.create_task_with_trajectory(state, config_snapshot={})
    store.finalize_task(
        task_id=state.task_id,
        final_state=state.advance(status="done"),
        status="done",
        termination_reason="answer",
        final_answer="done",
    )

    corpus = {
        "version": "0.4-test",
        "cases": [
            {
                "id": "stored_only",
                "kind": "known_good",
                "evidence_level": "stored_live",
                "prompt": "summarize stored",
                "task_id": "task-live-1",
                "expected_quality_signal": "read-only summary",
                "expected": {"status": "done"},
            }
        ],
    }
    corpus_path = tmp_path / "corpus.yaml"
    corpus_path.write_text(yaml.safe_dump(corpus), encoding="utf-8")

    report = run_eval_suite(
        corpus_path=corpus_path,
        sqlite_path=db_path,
        include_fresh_live=False,
        workdir=tmp_path / "suite",
    )

    assert len(report.case_results) == 1
    assert report.case_results[0].status == "pass"
    assert report.case_results[0].task_id == "task-live-1"
    assert report.case_results[0].trajectory_report is not None


def test_eval_suite_marks_missing_stored_live_without_failing(tmp_path: Path) -> None:
    corpus = {
        "version": "0.4-test",
        "cases": [
            {
                "id": "missing_live",
                "evidence_level": "stored_live",
                "prompt": "missing",
                "task_id": "does-not-exist",
                "expected_quality_signal": "missing stays missing",
            }
        ],
    }
    corpus_path = tmp_path / "corpus.yaml"
    corpus_path.write_text(yaml.safe_dump(corpus), encoding="utf-8")

    report = run_eval_suite(
        corpus_path=corpus_path,
        sqlite_path=tmp_path / "empty.db",
        workdir=tmp_path / "suite",
    )

    assert report.case_results[0].status == "missing"
    assert report.totals_by_status.get("fail", 0) == 0


def test_cli_eval_suite_json_is_single_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["setup"])

    result = runner.invoke(
        app,
        ["eval-suite", "--json", "--corpus", str(FIXTURE_CORPUS)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["corpus_version"] == "0.4"
    assert payload["totals_by_status"]["pass"] >= 6
    assert payload["promotion"]["recommend_enforce"] is False
    assert "product proof" in payload["disclaimer"]
    assert payload["include_fresh_live"] is False


def test_cli_eval_suite_honors_fresh_live_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HEURIVA_EVAL_SUITE_FRESH_LIVE", "1")
    runner = CliRunner()
    runner.invoke(app, ["setup"])

    result = runner.invoke(
        app,
        ["eval-suite", "--json", "--corpus", str(FIXTURE_CORPUS)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["include_fresh_live"] is True
    by_id = {case["case_id"]: case for case in payload["case_results"]}
    assert by_id["fresh_live_opt_in"]["status"] == "skipped"
    assert "opted in" in by_id["fresh_live_opt_in"]["notes"]


def test_default_packaged_corpus_loads() -> None:
    corpus, path = load_eval_corpus()
    assert corpus.version == "0.4"
    assert path.name == "v04_eval_corpus.yaml"
    assert any(case.harness == "forbidden_search_guard" for case in corpus.cases)
