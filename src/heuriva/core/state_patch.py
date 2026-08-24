from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from heuriva.core.observation import ErrorInfo, SourceRef
from heuriva.core.state import EvidenceItem, FailureRecord, HypothesisItem, KnownItem


class StatePatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    known_add: tuple[KnownItem, ...] = ()
    unknowns_add: tuple[str, ...] = ()
    unknowns_resolve: tuple[str, ...] = ()
    hypotheses_add: tuple[HypothesisItem, ...] = ()
    evidence_add: tuple[EvidenceItem, ...] = ()
    unresolved_add: tuple[str, ...] = ()
    unresolved_resolve: tuple[str, ...] = ()
    failures_add: tuple[FailureRecord, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class OperationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = ""
    patch: StatePatch | None = None
    citations: tuple[SourceRef, ...] = ()
    final_answer: str | None = None
    error: ErrorInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
