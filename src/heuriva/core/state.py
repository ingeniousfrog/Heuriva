from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import new_id, non_empty, utc_now
from heuriva.core.task_contract import TaskContract


class StateStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    MAX_STEPS_REACHED = "max_steps_reached"
    INTERRUPTED = "interrupted"


class KnownItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    content: str
    origin: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator("content", "origin")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "known item field")


class HypothesisItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    content: str
    evidence_refs: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("content")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "hypothesis content")


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    content: str
    source_type: str
    source_ref: str
    retrieved_at: datetime = Field(default_factory=utc_now)
    query: str | None = None
    relevance_verdict: str = "unassessed"
    supports_criteria: tuple[str, ...] = ()
    assessment_origin: str = "legacy"

    @field_validator("content", "source_type", "source_ref")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "evidence field")

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("relevance_verdict", "assessment_origin")
    @classmethod
    def _non_empty_metadata(cls, value: str) -> str:
        return non_empty(value, "evidence metadata field")

    @field_validator("supports_criteria")
    @classmethod
    def _clean_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)


class FailureRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retryable: bool
    step_index: int = Field(ge=0)

    @field_validator("code", "message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "failure field")


class CognitiveState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    task_id: str
    revision_index: int = Field(ge=0)
    step_index: int = Field(ge=0)
    goal: str
    task_contract: TaskContract = Field(default_factory=TaskContract)
    constraints: tuple[str, ...] = ()
    known: tuple[KnownItem, ...] = ()
    unknowns: tuple[str, ...] = ()
    hypotheses: tuple[HypothesisItem, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    unresolved: tuple[str, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    history_refs: tuple[str, ...] = ()
    status: StateStatus = StateStatus.RUNNING
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id", "goal")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "state field")

    @field_validator("constraints", "unknowns", "unresolved", "history_refs")
    @classmethod
    def _clean_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)

    @classmethod
    def new(
        cls,
        *,
        task_id: str,
        goal: str,
        task_contract: TaskContract | None = None,
        constraints: tuple[str, ...] = (),
        unknowns: tuple[str, ...] = (),
    ) -> CognitiveState:
        return cls(
            task_id=task_id,
            revision_index=0,
            step_index=0,
            goal=goal,
            task_contract=task_contract or TaskContract(),
            constraints=constraints,
            unknowns=unknowns,
            unresolved=(),
        )

    def advance(
        self, *, status: StateStatus | str | None = None, **updates: object
    ) -> CognitiveState:
        return self._replace(
            revision_index=self.revision_index + 1,
            step_index=self.step_index + 1,
            status=status or self.status,
            **updates,
        )

    def terminal(self, *, status: StateStatus | str, **updates: object) -> CognitiveState:
        return self._replace(
            revision_index=self.revision_index + 1,
            step_index=self.step_index,
            status=status,
            **updates,
        )

    def _replace(self, **updates: object) -> CognitiveState:
        data = self.model_dump(mode="python")
        data.update(updates)
        data["id"] = new_id()
        data["created_at"] = utc_now()
        return CognitiveState.model_validate(data)
