from __future__ import annotations

import pytest

from heuriva.core.decision import AnalyzeParams, DecisionDraft, SearchParams, bind_decision
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.runtime.executor_router import ExecutorRouter
from heuriva.trace import render_step


def test_router_is_deterministic_and_search_can_be_disabled() -> None:
    router = ExecutorRouter(search_enabled=False)

    assert router.available_operators() == (Operator.ANALYZE, Operator.ANSWER)

    state = CognitiveState.new(task_id="task-1", goal="Explain")
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.SEARCH,
            objective="find",
            reason="needs evidence",
            success_criteria=("result",),
            params=SearchParams(query="x"),
            confidence=0.4,
        ),
        state,
    )

    with pytest.raises(ValueError, match="not available"):
        router.resolve(decision)


def test_trace_default_is_concise() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANALYZE,
            objective="inspect",
            reason="understand the task",
            success_criteria=("summary",),
            params=AnalyzeParams(focus="task"),
            confidence=0.5,
        ),
        state,
    )

    line = render_step(decision=decision, observation_content="analysis complete", trace=False)

    assert "ANALYZE" in line
    assert "understand the task" in line
    assert "analysis complete" not in line


def test_trace_mode_includes_observation_content() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANALYZE,
            objective="inspect",
            reason="understand the task",
            success_criteria=("summary",),
            params=AnalyzeParams(focus="task"),
            confidence=0.5,
        ),
        state,
    )

    line = render_step(decision=decision, observation_content="analysis complete", trace=True)

    assert "analysis complete" in line
