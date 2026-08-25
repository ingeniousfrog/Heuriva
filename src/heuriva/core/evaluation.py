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


class JudgeVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PARSE_FAILURE = "parse_failure"
    ERROR = "error"
    NOT_RUN = "not_run"


class DisagreementBucket(StrEnum):
    AGREE = "agree"
    DETERMINISTIC_PASS_JUDGE_FAIL = "deterministic_pass_judge_fail"
    DETERMINISTIC_FAIL_JUDGE_PASS = "deterministic_fail_judge_pass"
    MANUAL_REVIEW_NEEDED = "manual_review_needed"
    JUDGE_UNAVAILABLE = "judge_unavailable"


class JudgeProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    base_url: str
    prompt_version: str
    prompt_hash: str
    timestamp: str
    task_id: str
    trajectory_id: str | None = None
    case_id: str | None = None
    attempt_count: int = Field(default=1, ge=1)
    call_count: int = Field(default=1, ge=0)
    failure_code: str | None = None

    @field_validator("model", "base_url", "prompt_version", "prompt_hash", "timestamp", "task_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "judge provenance field")

    @field_validator("trajectory_id", "case_id", "failure_code")
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return non_empty(value, "judge provenance optional field")


class JudgeAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: JudgeVerdict
    reason: str
    failed_criteria: tuple[str, ...] = ()
    manual_review_needed: bool = False
    assessment_origin: str = "fresh_judge"
    provenance: JudgeProvenance

    @field_validator("reason", "assessment_origin")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "judge assessment field")

    @field_validator("failed_criteria")
    @classmethod
    def _clean_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)


class DisagreementReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket: DisagreementBucket
    deterministic_verdict: str
    judge_verdict: str
    notes: str = ""

    @field_validator("deterministic_verdict", "judge_verdict")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "disagreement verdict field")


class PromotionCheckAdvice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str
    mode_advice: str
    rationale: str

    @field_validator("check_id", "mode_advice", "rationale")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "promotion check advice field")


class PromotionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommend_enforce: bool
    allow_enforce_checks: tuple[PromotionCheckAdvice, ...] = ()
    keep_observe_checks: tuple[PromotionCheckAdvice, ...] = ()
    rationale: str
    disagreement_bucket: DisagreementBucket | None = None

    @field_validator("rationale")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "promotion rationale")


class VerifyGateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enter_verify_design: bool
    distinct_leak_task_count: int = Field(default=0, ge=0)
    conditions_met: dict[str, bool] = Field(default_factory=dict)
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "verify gate rationale")
