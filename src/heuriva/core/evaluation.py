from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import non_empty


class RelevanceVerdict(StrEnum):
    RELEVANT = "relevant"
    PARTIAL = "partial"
    IRRELEVANT = "irrelevant"
    UNASSESSED = "unassessed"


class CompletionVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_ASSESSED = "not_assessed"


class CriterionAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: str
    verdict: CompletionVerdict
    reason: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator("criterion", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "criterion assessment field")


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = Field(ge=1)
    verdict: RelevanceVerdict
    supports_criteria: tuple[str, ...] = ()
    reason: str
    assessment_origin: str = "deterministic"

    @field_validator("reason", "assessment_origin")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "candidate assessment field")

    @field_validator("supports_criteria")
    @classmethod
    def _clean_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)


class CompletionAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: CompletionVerdict
    criterion_results: tuple[CriterionAssessment, ...] = ()
    failed_criteria: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    feedback: str = ""
    assessment_origin: str = "deterministic"

    @field_validator("failed_criteria", "evidence_refs")
    @classmethod
    def _clean_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)

    @field_validator("assessment_origin")
    @classmethod
    def _non_empty_origin(cls, value: str) -> str:
        return non_empty(value, "completion assessment origin")
