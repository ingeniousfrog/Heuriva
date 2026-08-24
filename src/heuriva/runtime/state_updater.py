from __future__ import annotations

from typing import Protocol, TypeVar

from heuriva.core.state import CognitiveState, FailureRecord
from heuriva.core.state_patch import StatePatch


class ContentModel(Protocol):
    content: str


TContentModel = TypeVar("TContentModel", bound=ContentModel)


class StateUpdater:
    def apply(
        self, state: CognitiveState, patch: StatePatch | None, *, history_ref: str
    ) -> CognitiveState:
        if patch is None:
            return state.advance(history_refs=state.history_refs + (history_ref,))
        self._validate_evidence_refs(state, patch)
        evidence = _dedupe_by_content(state.evidence, patch.evidence_add)
        known = _dedupe_by_content(state.known, patch.known_add)
        hypotheses = _dedupe_by_content(state.hypotheses, patch.hypotheses_add)
        unknowns = _resolve_then_add(state.unknowns, patch.unknowns_resolve, patch.unknowns_add)
        unresolved = _resolve_then_add(
            state.unresolved,
            patch.unresolved_resolve,
            patch.unresolved_add,
        )
        confidence = patch.confidence if patch.confidence is not None else state.confidence
        return state.advance(
            known=known,
            unknowns=unknowns,
            hypotheses=hypotheses,
            evidence=evidence,
            unresolved=unresolved,
            failures=_dedupe_failures(state.failures, patch.failures_add),
            confidence=confidence,
            history_refs=state.history_refs + (history_ref,),
        )

    @staticmethod
    def _validate_evidence_refs(state: CognitiveState, patch: StatePatch) -> None:
        evidence_ids = {item.id for item in state.evidence} | {
            item.id for item in patch.evidence_add
        }
        for item in patch.known_add:
            if item.origin != "task_input" and not item.evidence_refs:
                raise ValueError("known item must reference evidence unless origin=task_input")
            if not set(item.evidence_refs).issubset(evidence_ids):
                raise ValueError("known item references missing evidence")
        for hypothesis in patch.hypotheses_add:
            if hypothesis.evidence_refs and not set(hypothesis.evidence_refs).issubset(
                evidence_ids
            ):
                raise ValueError("hypothesis references missing evidence")


def _dedupe_by_content(
    existing: tuple[TContentModel, ...], incoming: tuple[TContentModel, ...]
) -> tuple[TContentModel, ...]:
    seen = {item.content.strip().lower() for item in existing}
    result = list(existing)
    for item in incoming:
        key = item.content.strip().lower()
        if key not in seen:
            result.append(item)
            seen.add(key)
    return tuple(result)


def _dedupe_failures(
    existing: tuple[FailureRecord, ...], incoming: tuple[FailureRecord, ...]
) -> tuple[FailureRecord, ...]:
    seen = {(item.code, item.message, item.step_index) for item in existing}
    result = list(existing)
    for item in incoming:
        key = (item.code, item.message, item.step_index)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return tuple(result)


def _resolve_then_add(
    existing: tuple[str, ...],
    resolve: tuple[str, ...],
    incoming: tuple[str, ...],
) -> tuple[str, ...]:
    resolved = {item.strip().lower() for item in resolve}
    result = [item for item in existing if item.strip().lower() not in resolved]
    seen = {item.strip().lower() for item in result}
    for item in incoming:
        stripped = item.strip()
        key = stripped.lower()
        if stripped and key not in seen:
            result.append(stripped)
            seen.add(key)
    return tuple(result)
