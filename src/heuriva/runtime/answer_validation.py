from __future__ import annotations

import re
from dataclasses import dataclass

from heuriva.core.observation import ErrorInfo, SourceRef
from heuriva.core.state import CognitiveState, EvidenceItem

LABEL_RE = re.compile(r"\[S([1-9]\d*)\]")


@dataclass(frozen=True)
class EvidenceBinding:
    label: str
    evidence: EvidenceItem
    source: SourceRef


@dataclass(frozen=True)
class AnswerValidationResult:
    ok: bool
    rendered_answer: str
    citations: tuple[SourceRef, ...] = ()
    error: ErrorInfo | None = None


def build_evidence_bindings(state: CognitiveState) -> tuple[EvidenceBinding, ...]:
    return tuple(
        EvidenceBinding(label=f"S{index}", evidence=item, source=_source_from_evidence(item, index))
        for index, item in enumerate(state.evidence, start=1)
    )


def validate_answer_citations(answer: str, state: CognitiveState) -> AnswerValidationResult:
    stripped = answer.strip()
    bindings = build_evidence_bindings(state)
    if not bindings:
        return AnswerValidationResult(ok=True, rendered_answer=stripped)
    labels = tuple(f"S{match}" for match in LABEL_RE.findall(stripped))
    if not labels:
        return _invalid(stripped, "answer must cite at least one saved evidence label")
    binding_by_label = {binding.label: binding for binding in bindings}
    unknown = tuple(label for label in labels if label not in binding_by_label)
    if unknown:
        return _invalid(stripped, f"answer cited unknown evidence label: {unknown[0]}")
    selected: list[EvidenceBinding] = []
    seen: set[str] = set()
    for label in labels:
        if label not in seen:
            selected.append(binding_by_label[label])
            seen.add(label)
    citations = tuple(binding.source for binding in selected)
    return AnswerValidationResult(
        ok=True,
        rendered_answer=_append_sources(stripped, selected),
        citations=citations,
    )


def _invalid(answer: str, message: str) -> AnswerValidationResult:
    return AnswerValidationResult(
        ok=False,
        rendered_answer=answer,
        error=ErrorInfo(code="answer_validation_error", message=message, retryable=True),
    )


def _append_sources(answer: str, selected: list[EvidenceBinding]) -> str:
    source_lines = [
        f"[{binding.label}] {binding.source.title} - {binding.source.url}" for binding in selected
    ]
    return f"{answer}\n\nSources:\n" + "\n".join(source_lines)


def _source_from_evidence(item: EvidenceItem, rank: int) -> SourceRef:
    title, snippet = _split_evidence_content(item.content, rank)
    return SourceRef(
        title=title,
        url=item.source_ref,
        snippet=snippet,
        rank=rank,
        retrieved_at=item.retrieved_at,
    )


def _split_evidence_content(content: str, rank: int) -> tuple[str, str]:
    title, separator, snippet = content.partition(":")
    if separator and title.strip():
        return title.strip(), snippet.strip()
    return f"Evidence {rank}", content.strip()
