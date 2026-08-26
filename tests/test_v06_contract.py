from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from heuriva.cli import app
from heuriva.config import AppConfig, QualityConfig
from heuriva.core.evaluation import CompletionVerdict
from heuriva.core.state import CognitiveState
from heuriva.core.task_contract import (
    Criterion,
    CriterionKind,
    TaskContract,
)
from heuriva.runtime.completion_validation import CompletionValidator
from heuriva.runtime.engine import RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import FakeController, FakeExecutor, make_answer_decision


def test_criterion_parse_legacy_and_dsl() -> None:
    legacy = Criterion.parse("mention safety")
    assert legacy.kind is CriterionKind.MUST_INCLUDE
    assert legacy.value == "mention safety"
    assert legacy.display() == "must_include:mention safety"

    exact = Criterion.parse("exact_answer:OK")
    assert exact.kind is CriterionKind.EXACT_ANSWER
    assert exact.value == "OK"
    assert exact.normalize == ("trim", "collapse_whitespace")

    forbidden = Criterion.parse("must_not_include:SECRET")
    assert forbidden.kind is CriterionKind.MUST_NOT_INCLUDE
    assert forbidden.value == "SECRET"

    structured = Criterion.parse({"kind": "must_include", "value": "tradeoffs"})
    assert structured.kind is CriterionKind.MUST_INCLUDE
    assert structured.value == "tradeoffs"


def test_criterion_rejects_unknown_fields_and_bad_normalize() -> None:
    with pytest.raises(ValidationError):
        Criterion.model_validate({"kind": "must_include", "value": "x", "unknown": True})
    with pytest.raises(ValidationError):
        Criterion.model_validate({"kind": "must_include", "value": "x", "normalize": ["trim"]})
    with pytest.raises(ValidationError):
        Criterion.model_validate({"kind": "exact_answer", "value": "OK", "normalize": ["stem"]})


def test_task_contract_coerces_legacy_strings_and_dedupes() -> None:
    contract = TaskContract.model_validate(
        {
            "criteria": (
                " Mention safety ",
                "Mention safety",
                "must_include:Mention safety",
            ),
            "search_policy": "forbidden",
            "evidence_requirement": "required",
            "origin": "user",
        }
    )
    assert len(contract.criteria) == 1
    assert contract.criteria[0].kind is CriterionKind.MUST_INCLUDE
    assert contract.criteria[0].value == "Mention safety"

    legacy_state = CognitiveState.model_validate(
        {
            "task_id": "task-legacy",
            "revision_index": 0,
            "step_index": 0,
            "goal": "Explain",
            "task_contract": {"criteria": ["mention safety"], "origin": "user"},
        }
    )
    assert legacy_state.task_contract.criteria[0].kind is CriterionKind.MUST_INCLUDE
    assert legacy_state.task_contract.criteria[0].value == "mention safety"


def test_exact_answer_fails_on_extra_text() -> None:
    validator = CompletionValidator(
        QualityConfig.model_validate({"completion_check_mode": "observe"})
    )
    state = CognitiveState.new(
        task_id="task-exact",
        goal="Return exactly OK",
        task_contract=TaskContract.from_user(criteria=("exact_answer:OK",)),
    )
    assessment = validator.assess(answer="OK\nV03BLOCKTOKEN", state=state)
    assert assessment is not None
    assert assessment.verdict is CompletionVerdict.FAIL
    assert assessment.criterion_results[0].kind == "exact_answer"
    assert "exact_answer:OK" in assessment.failed_criteria

    passing = validator.assess(answer="  OK  ", state=state)
    assert passing is not None
    assert passing.verdict is CompletionVerdict.PASS


def test_must_not_include_fails_when_present() -> None:
    validator = CompletionValidator(
        QualityConfig.model_validate({"completion_check_mode": "observe"})
    )
    state = CognitiveState.new(
        task_id="task-forbid",
        goal="No secrets",
        task_contract=TaskContract.from_user(
            criteria=("must_include:OK", "must_not_include:SECRETTOKEN")
        ),
    )
    failed = validator.assess(answer="OK SECRETTOKEN", state=state)
    assert failed is not None
    assert failed.verdict is CompletionVerdict.FAIL
    assert any(item.kind == "must_not_include" for item in failed.criterion_results)

    passed = validator.assess(answer="Status is OK", state=state)
    assert passed is not None
    assert passed.verdict is CompletionVerdict.PASS


def test_legacy_string_criterion_behavior_unchanged() -> None:
    validator = CompletionValidator(
        QualityConfig.model_validate({"completion_check_mode": "observe"})
    )
    state = CognitiveState.new(
        task_id="task-legacy-assess",
        goal="Explain safety",
        task_contract=TaskContract.from_user(criteria=("mention safety",)),
    )
    assessment = validator.assess(
        answer="A safe answer that mentions safety",
        state=state,
    )
    assert assessment is not None
    assert assessment.verdict is CompletionVerdict.PASS
    assert assessment.criterion_results[0].kind == "must_include"


def test_answer_prompt_payload_includes_structured_criteria() -> None:
    from heuriva.core.decision import bind_decision
    from heuriva.executors.llm import _answer_messages

    state = CognitiveState.new(
        task_id="task-prompt",
        goal="Return exactly OK",
        task_contract=TaskContract.from_user(criteria=("exact_answer:OK",)),
    )
    decision = bind_decision(make_answer_decision("answer"), state)
    messages = _answer_messages(decision, state)
    payload = json.loads(messages[1]["content"])
    assert payload["structured_criteria"] == [
        {"kind": "exact_answer", "value": "OK", "display": "exact_answer:OK"}
    ]
    assert payload["task_contract"]["criteria"][0]["kind"] == "exact_answer"


def test_cli_run_help_documents_structured_criteria() -> None:
    result = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}).invoke(
        app, ["run", "--help"]
    )
    assert result.exit_code == 0
    help_text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
    assert "--criterion-exact" in help_text or "criterion-exact" in help_text
    assert "must-not" in help_text or "must_not" in help_text
    assert "exact_answer" in help_text


def test_engine_records_kind_on_completion_assessment(tmp_path: Path) -> None:
    from heuriva.core.operator import Operator

    store = SQLiteStore(tmp_path / "memory.db")
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
        executors={
            Operator.ANSWER: FakeExecutor("final", final_answer="OK\nEXTRA"),
        },
    )
    result = engine.run("Return exactly OK", criteria=("exact_answer:OK",))
    data = store.get_trajectory(result.task_id)
    assessment = data["steps"][0]["observation"]["metadata"]["completion_assessment"]
    assert assessment["verdict"] == "fail"
    assert assessment["criterion_results"][0]["kind"] == "exact_answer"
