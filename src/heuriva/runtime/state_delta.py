from __future__ import annotations

from dataclasses import dataclass

from heuriva.core.state import (
    CognitiveState,
    EvidenceItem,
    FailureRecord,
    KnownItem,
    StateStatus,
)


@dataclass(frozen=True)
class StateDelta:
    added_evidence: tuple[EvidenceItem, ...]
    added_known: tuple[KnownItem, ...]
    resolved_unknowns: tuple[str, ...]
    resolved_unresolved: tuple[str, ...]
    added_failures: tuple[FailureRecord, ...]
    confidence_before: float
    confidence_after: float
    became_done: bool = False

    @property
    def added_evidence_count(self) -> int:
        return len(self.added_evidence)

    @property
    def added_known_count(self) -> int:
        return len(self.added_known)

    @property
    def added_failure_codes(self) -> tuple[str, ...]:
        return tuple(failure.code for failure in self.added_failures)

    @property
    def material_progress(self) -> bool:
        return bool(
            self.added_evidence
            or self.added_known
            or self.resolved_unknowns
            or self.resolved_unresolved
            or self.added_failures
            or self.became_done
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.added_evidence_count:
            parts.append(f"+{self.added_evidence_count} evidence")
        if self.added_known_count:
            parts.append(f"+{self.added_known_count} known")
        if self.resolved_unknowns:
            parts.append(f"resolved {len(self.resolved_unknowns)} unknown")
        if self.resolved_unresolved:
            parts.append(f"resolved {len(self.resolved_unresolved)} unresolved")
        if self.added_failures:
            parts.append(f"+{len(self.added_failures)} failure")
        if self.became_done:
            parts.append("done")
        if not parts:
            parts.append("no material progress")
        line = f"delta: {', '.join(parts)}"
        if self.confidence_before != self.confidence_after:
            line = f"{line}, confidence {self.confidence_before:.1f} -> {self.confidence_after:.1f}"
        return line

    def detail_lines(self, *, limit: int = 5) -> tuple[str, ...]:
        lines: list[str] = []
        for evidence in self.added_evidence[:limit]:
            lines.append(f"evidence + {evidence.source_ref}")
        for item in self.added_known[:limit]:
            lines.append(f"known + {item.content[:160]}")
        for resolved_unknown in self.resolved_unknowns[:limit]:
            lines.append(f"unknown resolved {resolved_unknown}")
        for resolved_item in self.resolved_unresolved[:limit]:
            lines.append(f"unresolved resolved {resolved_item}")
        for failure in self.added_failures[:limit]:
            lines.append(f"failure + {failure.code}: {failure.message[:160]}")
        return tuple(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "material_progress": self.material_progress,
            "added_evidence_count": self.added_evidence_count,
            "added_known_count": self.added_known_count,
            "resolved_unknowns": self.resolved_unknowns,
            "resolved_unresolved": self.resolved_unresolved,
            "added_failure_codes": self.added_failure_codes,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "became_done": self.became_done,
            "details": self.detail_lines(),
        }


def calculate_state_delta(before: CognitiveState, after: CognitiveState) -> StateDelta:
    before_evidence_keys = {_evidence_key(item) for item in before.evidence}
    before_known_keys = {_known_key(item) for item in before.known}
    before_unknowns = {_string_key(item): item for item in before.unknowns}
    before_unresolved = {_string_key(item): item for item in before.unresolved}
    before_failure_keys = {_failure_key(item) for item in before.failures}

    added_evidence = tuple(
        item for item in after.evidence if _evidence_key(item) not in before_evidence_keys
    )
    added_known = tuple(
        item
        for item in after.known
        if _known_key(item) not in before_known_keys and bool(item.evidence_refs)
    )
    after_unknown_keys = {_string_key(item) for item in after.unknowns}
    after_unresolved_keys = {_string_key(item) for item in after.unresolved}
    resolved_unknowns = tuple(
        original for key, original in before_unknowns.items() if key not in after_unknown_keys
    )
    resolved_unresolved = tuple(
        original for key, original in before_unresolved.items() if key not in after_unresolved_keys
    )
    added_failures = tuple(
        item for item in after.failures if _failure_key(item) not in before_failure_keys
    )
    return StateDelta(
        added_evidence=added_evidence,
        added_known=added_known,
        resolved_unknowns=resolved_unknowns,
        resolved_unresolved=resolved_unresolved,
        added_failures=added_failures,
        confidence_before=before.confidence,
        confidence_after=after.confidence,
        became_done=before.status is not StateStatus.DONE and after.status is StateStatus.DONE,
    )


def _evidence_key(item: EvidenceItem) -> tuple[str, str]:
    return (_string_key(item.source_ref), _string_key(item.content))


def _known_key(item: KnownItem) -> str:
    return _string_key(item.content)


def _failure_key(item: FailureRecord) -> tuple[str, str, int]:
    return (item.code, item.message, item.step_index)


def _string_key(value: str) -> str:
    return " ".join(value.strip().lower().split())
