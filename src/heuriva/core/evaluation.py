from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import non_empty
from heuriva.core.task_contract import EvidenceRequirement, SearchPolicy


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


class EvidenceLevel(StrEnum):
    SYNTHETIC = "synthetic"
    FAKE_INTEGRATION = "fake_integration"
    STORED_LIVE = "stored_live"
    FRESH_LIVE = "fresh_live"


class CaseKind(StrEnum):
    KNOWN_GOOD = "known_good"
    KNOWN_BAD = "known_bad"


class CaseResultStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    MISSING = "missing"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


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


class ExpectedCaseSignals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str | None = None
    termination_reason: str | None = None
    search_provider_calls: int | None = Field(default=None, ge=0)
    search_guard_reasons: tuple[str, ...] = ()
    min_search_guards: int | None = Field(default=None, ge=0)
    min_raw_candidates: int | None = Field(default=None, ge=0)
    min_accepted_evidence: int | None = Field(default=None, ge=0)
    min_rejected_candidates: int | None = Field(default=None, ge=0)
    citation_validation: str | None = None
    completion_verdict: str | None = None

    @field_validator("search_guard_reasons")
    @classmethod
    def _clean_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)


class EvalCorpusCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    prompt: str
    evidence_level: EvidenceLevel
    kind: CaseKind = CaseKind.KNOWN_GOOD
    criteria: tuple[str, ...] = ()
    search_policy: SearchPolicy = SearchPolicy.AUTO
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.OPTIONAL
    expected_quality_signal: str
    expected: ExpectedCaseSignals = Field(default_factory=ExpectedCaseSignals)
    harness: str | None = None
    task_id: str | None = None
    notes: str = ""

    @field_validator("id", "prompt", "expected_quality_signal")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "eval corpus case field")

    @field_validator("criteria")
    @classmethod
    def _clean_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)

    @field_validator("task_id", "harness")
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return non_empty(value, "eval corpus optional field")


class EvalCorpus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    cases: tuple[EvalCorpusCase, ...]

    @field_validator("version")
    @classmethod
    def _non_empty_version(cls, value: str) -> str:
        return non_empty(value, "corpus version")

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, values: tuple[EvalCorpusCase, ...]) -> tuple[EvalCorpusCase, ...]:
        if not values:
            raise ValueError("corpus must include at least one case")
        seen: set[str] = set()
        for case in values:
            if case.id in seen:
                raise ValueError(f"duplicate corpus case id: {case.id}")
            seen.add(case.id)
        return values
