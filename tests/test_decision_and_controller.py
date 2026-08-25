from __future__ import annotations

import pytest

from heuriva.controller.llm_controller import LLMController
from heuriva.core.decision import (
    DecisionDraft,
    SearchParams,
    bind_decision,
    normalize_draft_payload,
)
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.testing.fakes import QueueModelClient


def test_decision_draft_forbids_executor_kind() -> None:
    with pytest.raises(ValueError):
        DecisionDraft.model_validate(
            {
                "operator": "SEARCH",
                "objective": "find sources",
                "reason": "needs evidence",
                "success_criteria": ["has URL"],
                "params": {"query": "heuriva"},
                "confidence": 0.4,
                "executor_kind": "search",
            }
        )


def test_decision_binds_runtime_fields_after_validation() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    draft = DecisionDraft(
        operator=Operator.SEARCH,
        objective="find sources",
        reason="needs current evidence",
        success_criteria=("result with URL",),
        params=SearchParams(query="adaptive cognitive runtime"),
        confidence=0.5,
    )

    decision = bind_decision(draft, state)

    assert decision.task_id == state.task_id
    assert decision.state_id == state.id
    assert decision.step_index == state.step_index
    assert decision.policy_refs == ()
    assert not hasattr(decision, "executor_kind")


def test_decision_normalizes_single_success_criterion_string() -> None:
    draft = DecisionDraft.model_validate(
        normalize_draft_payload(
            {
                "operator": "ANSWER",
                "objective": "answer now",
                "reason": "enough information",
                "success_criteria": "final answer",
                "params": {},
                "confidence": 0.8,
            }
        )
    )

    assert draft.success_criteria == ("final answer",)


def test_controller_accepts_string_success_criteria_without_repair_event() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    model = QueueModelClient(
        [
            {
                "operator": "ANSWER",
                "objective": "answer now",
                "reason": "enough information",
                "success_criteria": "final answer",
                "params": {},
                "confidence": 0.8,
            },
        ]
    )
    controller = LLMController(model_client=model)

    decision, events = controller.select(
        state=state,
        available_operators=(Operator.ANALYZE, Operator.ANSWER),
        runtime_limits={"max_steps": 2},
    )

    assert decision.operator is Operator.ANSWER
    assert decision.success_criteria == ("final answer",)
    assert events == []
    assert len(model.calls) == 1


def test_controller_repairs_malformed_json_once() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    model = QueueModelClient(
        [
            "not json",
            {
                "operator": "ANSWER",
                "objective": "answer now",
                "reason": "enough information",
                "success_criteria": ["final answer"],
                "params": {},
                "confidence": 0.8,
            },
        ]
    )
    controller = LLMController(model_client=model)

    decision, events = controller.select(
        state=state,
        available_operators=(Operator.ANALYZE, Operator.ANSWER),
        runtime_limits={"max_steps": 2},
    )

    assert decision.operator is Operator.ANSWER
    assert len(events) == 1
    assert events[0].event_type == "controller_parse_error"
    assert len(model.calls) == 2


def test_controller_fails_after_repair_exhausted() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    controller = LLMController(model_client=QueueModelClient(["not json", "still not json"]))

    with pytest.raises(ValueError, match="Controller did not return valid JSON"):
        controller.select(
            state=state,
            available_operators=(Operator.ANALYZE,),
            runtime_limits={"max_steps": 2},
        )
