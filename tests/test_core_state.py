from __future__ import annotations

import pytest
from pydantic import ValidationError

from heuriva.core.observation import SourceRef
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, EvidenceItem, KnownItem, StateStatus
from heuriva.core.state_patch import StatePatch
from heuriva.runtime.state_updater import StateUpdater


def test_state_is_immutable_and_json_round_trips() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Find product risks")

    with pytest.raises(ValidationError):
        state.revision_index = 9

    dumped = state.model_dump_json()
    restored = CognitiveState.model_validate_json(dumped)

    assert restored == state
    assert restored.constraints == ()
    assert restored.status is StateStatus.RUNNING


def test_state_updater_returns_new_state_without_mutating_original() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Ship v0.1")
    evidence = EvidenceItem(
        content="Search result summary",
        source_type="search",
        source_ref="https://example.com",
    )
    patch = StatePatch(
        evidence_add=(evidence,),
        known_add=(
            KnownItem(
                content="Users need local traces",
                origin="analysis",
                evidence_refs=(evidence.id,),
            ),
        ),
        confidence=0.7,
    )

    updated = StateUpdater().apply(state, patch, history_ref="step-0")

    assert state.known == ()
    assert updated.id != state.id
    assert updated.revision_index == state.revision_index + 1
    assert updated.step_index == state.step_index + 1
    assert updated.known[0].content == "Users need local traces"
    assert updated.history_refs == ("step-0",)


def test_known_items_need_evidence_unless_from_task_input() -> None:
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    patch = StatePatch(
        known_add=(KnownItem(content="Unsupported fact", origin="analysis"),),
    )

    with pytest.raises(ValueError, match="must reference evidence"):
        StateUpdater().apply(state, patch, history_ref="step-0")


def test_patch_deduplicates_content_and_keeps_order() -> None:
    state = CognitiveState.new(
        task_id="task-1",
        goal="Explain",
        unknowns=("cost",),
    )
    patch = StatePatch(
        unknowns_add=("cost", "latency", "cost"),
        unresolved_add=("risk", "risk"),
    )

    updated = StateUpdater().apply(state, patch, history_ref="step-0")

    assert updated.unknowns == ("cost", "latency")
    assert updated.unresolved == ("risk",)


def test_operator_values_are_limited_to_v01() -> None:
    assert [item.value for item in Operator] == ["ANALYZE", "SEARCH", "ANSWER"]

    with pytest.raises(ValueError):
        Operator("SHELL")


def test_source_ref_preserves_url() -> None:
    source = SourceRef(title="Result", url="https://example.com/a", snippet="summary")

    assert source.url == "https://example.com/a"
