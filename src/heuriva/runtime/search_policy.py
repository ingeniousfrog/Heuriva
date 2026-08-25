from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from heuriva.config import QualityConfig
from heuriva.core.decision import Decision, SearchParams
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.core.task_contract import SearchPolicy, SourceScope
from heuriva.redaction import redact_text


@dataclass(frozen=True)
class SearchGuardResult:
    reason: str
    payload: dict[str, object]
    available_operators: tuple[Operator, ...]


def evaluate_search_guard(
    *,
    state: CognitiveState,
    decision: Decision,
    committed_steps: Sequence[Any],
    quality: QualityConfig,
    base_available: tuple[Operator, ...],
) -> SearchGuardResult | None:
    if not isinstance(decision.params, SearchParams):
        return _guard(
            reason="invalid_search_params",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    contract = state.task_contract
    if contract.search_policy is SearchPolicy.FORBIDDEN:
        return _guard(
            reason="search_forbidden",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    if (
        contract.search_policy is not SearchPolicy.REQUIRED
        and decision.params.source_scope is not SourceScope.WEB
    ):
        return _guard(
            reason="source_scope_mismatch",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    if _search_step_count(committed_steps) >= quality.max_search_steps:
        return _guard(
            reason="search_budget_exhausted",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    if normalize_query(decision.params.query) in _previous_queries(committed_steps):
        return _guard(
            reason="duplicate_query",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    if not decision.params.evidence_need or not decision.params.expected_signal:
        return _guard(
            reason="missing_evidence_intent",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    if _no_relevant_search_streak(committed_steps) >= quality.max_no_relevant_search_steps:
        return _guard(
            reason="no_relevant_search_results",
            state=state,
            decision=decision,
            committed_steps=committed_steps,
            quality=quality,
            base_available=base_available,
        )
    return None


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _guard(
    *,
    reason: str,
    state: CognitiveState,
    decision: Decision,
    committed_steps: Sequence[Any],
    quality: QualityConfig,
    base_available: tuple[Operator, ...],
) -> SearchGuardResult:
    next_available = tuple(
        operator for operator in base_available if operator is not Operator.SEARCH
    )
    query = decision.params.query if isinstance(decision.params, SearchParams) else ""
    remaining = max(0, quality.max_search_steps - _search_step_count(committed_steps))
    return SearchGuardResult(
        reason=reason,
        payload={
            "reason": reason,
            "query": redact_text(query)[:240],
            "search_steps": _search_step_count(committed_steps),
            "remaining_search_steps": remaining,
            "next_available_operators": [operator.value for operator in next_available],
            "task_contract": state.task_contract.model_dump(mode="json"),
        },
        available_operators=next_available,
    )


def _search_step_count(committed_steps: Sequence[Any]) -> int:
    return sum(
        1
        for step in committed_steps
        if getattr(getattr(step, "decision", None), "operator", None) is Operator.SEARCH
    )


def _previous_queries(committed_steps: Sequence[Any]) -> set[str]:
    queries: set[str] = set()
    for step in committed_steps:
        step_decision = getattr(step, "decision", None)
        if (
            isinstance(step_decision, Decision)
            and step_decision.operator is Operator.SEARCH
            and isinstance(step_decision.params, SearchParams)
        ):
            queries.add(normalize_query(step_decision.params.query))
    return queries


def _no_relevant_search_streak(committed_steps: Sequence[Any]) -> int:
    streak = 0
    for step in reversed(committed_steps):
        step_decision = getattr(step, "decision", None)
        if getattr(step_decision, "operator", None) is not Operator.SEARCH:
            continue
        metadata = getattr(step.observation, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        if "accepted_evidence_count" not in metadata:
            continue
        if _metadata_int(metadata.get("accepted_evidence_count")) > 0:
            return streak
        streak += 1
    return streak


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
