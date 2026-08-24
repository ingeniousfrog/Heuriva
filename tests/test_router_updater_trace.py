from __future__ import annotations

import pytest

from heuriva.core.decision import AnalyzeParams, DecisionDraft, SearchParams, bind_decision
from heuriva.core.observation import ErrorInfo
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, EvidenceItem, FailureRecord, KnownItem
from heuriva.runtime.answer_validation import validate_answer_citations
from heuriva.runtime.executor_router import ExecutorRouter
from heuriva.runtime.state_delta import calculate_state_delta
from heuriva.trace import render_saved_trajectory, render_step


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


def test_state_delta_identifies_material_progress() -> None:
    state = CognitiveState(
        task_id="task-1",
        revision_index=0,
        step_index=0,
        goal="Explain",
        unknowns=("pricing",),
        unresolved=("risk",),
        confidence=0.2,
    )
    evidence = EvidenceItem(
        content="Pricing source: public pricing details",
        source_type="search",
        source_ref="https://example.com/pricing",
    )
    after = state.advance(
        evidence=(evidence,),
        known=(
            KnownItem(
                content="The product has public pricing.",
                origin="analysis",
                evidence_refs=(evidence.id,),
            ),
        ),
        unknowns=(),
        unresolved=(),
        failures=(
            FailureRecord(
                code="search_timeout",
                message="first provider timed out",
                retryable=True,
                step_index=0,
            ),
        ),
        confidence=0.6,
        history_refs=("observation-1",),
    )

    delta = calculate_state_delta(state, after)

    assert delta.material_progress is True
    assert delta.added_evidence_count == 1
    assert delta.added_known_count == 1
    assert delta.resolved_unknowns == ("pricing",)
    assert delta.resolved_unresolved == ("risk",)
    assert delta.added_failure_codes == ("search_timeout",)
    assert "delta: +1 evidence" in delta.summary()


def test_state_delta_ignores_bookkeeping_and_confidence_only() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    after = state.advance(confidence=0.8, history_refs=("observation-1",))

    delta = calculate_state_delta(state, after)

    assert delta.material_progress is False
    assert delta.summary() == "delta: no material progress, confidence 0.0 -> 0.8"


def test_trace_can_include_state_delta_without_observation_spam() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    evidence = EvidenceItem(
        content="Source: result",
        source_type="search",
        source_ref="https://example.com/source",
    )
    after = state.advance(evidence=(evidence,), history_refs=("observation-1",))
    delta = calculate_state_delta(state, after)
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.SEARCH,
            objective="find evidence",
            reason="need a source",
            success_criteria=("source URL",),
            params=SearchParams(query="heuriva"),
            confidence=0.5,
        ),
        state,
    )

    concise = render_step(
        decision=decision,
        observation_content="long search output",
        trace=False,
        state_delta=delta,
    )
    detailed = render_step(
        decision=decision,
        observation_content="long search output",
        trace=True,
        state_delta=delta,
    )

    assert "delta: +1 evidence" in concise
    assert "long search output" not in concise
    assert "https://example.com/source" in detailed


def test_answer_validator_requires_known_evidence_labels() -> None:
    state = CognitiveState(
        task_id="task-1",
        revision_index=0,
        step_index=0,
        goal="Explain",
        evidence=(
            EvidenceItem(
                content="Source one: useful fact",
                source_type="search",
                source_ref="https://example.com/one",
            ),
            EvidenceItem(
                content="Source two: another fact",
                source_type="search",
                source_ref="https://example.com/two",
            ),
        ),
    )

    valid = validate_answer_citations("Answer with support [S1].", state)
    missing = validate_answer_citations("Answer with no citation.", state)
    unknown = validate_answer_citations("Answer with invented citation [S9].", state)

    assert valid.ok is True
    assert [source.url for source in valid.citations] == ["https://example.com/one"]
    assert valid.rendered_answer.endswith("[S1] Source one - https://example.com/one")
    assert missing.error == ErrorInfo(
        code="answer_validation_error",
        message="answer must cite at least one saved evidence label",
        retryable=True,
    )
    assert unknown.error == ErrorInfo(
        code="answer_validation_error",
        message="answer cited unknown evidence label: S9",
        retryable=True,
    )


def test_saved_trace_summary_includes_elapsed_seconds() -> None:
    text = render_saved_trajectory(
        {
            "trajectory": {
                "task_id": "task-1",
                "status": "done",
                "termination_reason": "answer",
                "started_at": "2026-08-24T00:00:00+00:00",
                "completed_at": "2026-08-24T00:00:02.500000+00:00",
            },
            "steps": [],
            "events": [],
        },
        trace=True,
    )

    assert "elapsed_seconds: 2.5" in text
