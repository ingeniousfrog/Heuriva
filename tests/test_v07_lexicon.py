from __future__ import annotations

from pathlib import Path

from heuriva.config import AppConfig, QualityConfig
from heuriva.core.evaluation import CompletionVerdict
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.task_contract import TaskContract
from heuriva.runtime.completion_validation import CompletionValidator
from heuriva.runtime.engine import RuntimeEngine
from heuriva.runtime.quality_lexicon import (
    TERM_EQUIVALENTS,
    expand_criterion_terms,
    expand_text_terms,
)
from heuriva.storage.sqlite import SQLiteStore
from heuriva.testing.fakes import FakeController, FakeExecutor, make_answer_decision


def test_shared_lexicon_expands_safety_and_tradeoffs() -> None:
    safety = expand_criterion_terms("mention safety")
    assert "安全" in safety
    assert "未成年" in safety
    tradeoffs = expand_criterion_terms("mention tradeoffs")
    assert "权衡" in tradeoffs
    assert "代价" in tradeoffs
    assert TERM_EQUIVALENTS["safety"][0] == "safety"


def test_completion_and_relevance_share_safety_expansion() -> None:
    from_criterion = set(expand_criterion_terms("mention safety"))
    from_text = set(expand_text_terms("need safety evidence for the release"))
    assert "安全" in from_criterion
    assert "安全" in from_text
    assert from_criterion & from_text


def test_cn_semantic_answer_passes_safety_tradeoffs_must_include() -> None:
    validator = CompletionValidator(
        QualityConfig.model_validate({"completion_check_mode": "observe"})
    )
    state = CognitiveState.new(
        task_id="task-cn-lexicon",
        goal="Give a short answer about a safety-sensitive product decision.",
        task_contract=TaskContract.from_user(criteria=("mention safety", "mention tradeoffs")),
    )
    answer = "结论：有条件做。主风险是不可逆伤害与未成年人保护。主要代价是转化下降；权衡上偏保守。"
    assessment = validator.assess(answer=answer, state=state)
    assert assessment is not None
    assert assessment.verdict is CompletionVerdict.PASS


def test_cn_lexicon_harness_records_pass(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
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
        "Give a short answer about a safety-sensitive product decision.",
        criteria=("mention safety", "mention tradeoffs"),
    )
    data = store.get_trajectory(result.task_id)
    assessment = data["steps"][0]["observation"]["metadata"]["completion_assessment"]
    assert assessment["verdict"] == "pass"
