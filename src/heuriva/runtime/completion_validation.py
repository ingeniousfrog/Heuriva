from __future__ import annotations

import re
from dataclasses import dataclass

from heuriva.config import QualityConfig, QualityMode
from heuriva.core.evaluation import (
    CompletionAssessment,
    CompletionVerdict,
    CriterionAssessment,
)
from heuriva.core.observation import ErrorInfo
from heuriva.core.state import CognitiveState, FailureRecord
from heuriva.core.state_patch import OperationResult, StatePatch
from heuriva.core.task_contract import (
    Criterion,
    CriterionKind,
    EvidenceRequirement,
    SearchPolicy,
)
from heuriva.runtime.quality_lexicon import expand_criterion_terms


@dataclass(frozen=True)
class CompletionValidationResult:
    result: OperationResult
    assessment: CompletionAssessment | None


class CompletionValidator:
    def __init__(self, quality: QualityConfig) -> None:
        self.quality = quality

    def validate(
        self,
        *,
        result: OperationResult,
        state: CognitiveState,
        previous_completion_failures: int,
    ) -> CompletionValidationResult:
        answer = (result.final_answer or result.content).strip()
        assessment = self.assess(answer=answer, state=state)
        if assessment is None:
            return CompletionValidationResult(result=result, assessment=None)
        metadata = {
            **result.metadata,
            "completion_assessment": assessment.model_dump(mode="json"),
            "completion_check_mode": self.quality.completion_check_mode.value,
        }
        if (
            self.quality.completion_check_mode is not QualityMode.ENFORCE
            or assessment.verdict is CompletionVerdict.PASS
        ):
            return CompletionValidationResult(
                result=result.model_copy(update={"metadata": metadata}),
                assessment=assessment,
            )
        retryable = previous_completion_failures < self.quality.max_completion_repairs
        code = "completion_validation_error" if retryable else "completion_not_met"
        error = ErrorInfo(
            code=code,
            message=assessment.feedback or "answer did not satisfy the task contract",
            retryable=retryable,
            details={"failed_criteria": list(assessment.failed_criteria)},
        )
        blocked = OperationResult(
            content=result.content,
            patch=StatePatch(
                failures_add=(
                    FailureRecord(
                        code=error.code,
                        message=error.message,
                        retryable=error.retryable,
                        step_index=state.step_index,
                    ),
                )
            ),
            citations=result.citations,
            error=error,
            metadata=metadata,
        )
        return CompletionValidationResult(result=blocked, assessment=assessment)

    def assess(self, *, answer: str, state: CognitiveState) -> CompletionAssessment | None:
        if self.quality.completion_check_mode is QualityMode.OFF:
            return None
        contract = state.task_contract
        should_assess = bool(contract.criteria) or (
            contract.evidence_requirement is EvidenceRequirement.REQUIRED
            or contract.search_policy is SearchPolicy.REQUIRED
        )
        if not should_assess:
            return None
        failed_labels = tuple(item.display() for item in contract.criteria)
        if not answer:
            return CompletionAssessment(
                verdict=CompletionVerdict.FAIL,
                failed_criteria=failed_labels,
                feedback="answer was empty",
            )
        if (
            contract.evidence_requirement is EvidenceRequirement.REQUIRED
            or contract.search_policy is SearchPolicy.REQUIRED
        ) and not state.evidence:
            return CompletionAssessment(
                verdict=CompletionVerdict.INSUFFICIENT_EVIDENCE,
                failed_criteria=failed_labels,
                feedback="task contract requires accepted evidence before final answer",
            )
        criterion_results = tuple(
            self._assess_criterion(criterion=criterion, answer=answer, state=state)
            for criterion in contract.criteria
        )
        failed = tuple(
            item.criterion
            for item in criterion_results
            if item.verdict is not CompletionVerdict.PASS
        )
        if failed:
            return CompletionAssessment(
                verdict=CompletionVerdict.FAIL,
                criterion_results=criterion_results,
                failed_criteria=failed,
                evidence_refs=tuple(item.id for item in state.evidence),
                feedback="answer did not satisfy all task criteria",
            )
        return CompletionAssessment(
            verdict=CompletionVerdict.PASS,
            criterion_results=criterion_results,
            evidence_refs=tuple(item.id for item in state.evidence),
            feedback="answer satisfies deterministic task criteria",
        )

    @staticmethod
    def _assess_criterion(
        *,
        criterion: Criterion,
        answer: str,
        state: CognitiveState,
    ) -> CriterionAssessment:
        evidence_refs = tuple(item.id for item in state.evidence)
        label = criterion.display()
        kind = criterion.kind.value
        if criterion.kind is CriterionKind.EXACT_ANSWER:
            expected = _normalize_text(criterion.value, criterion.normalize)
            actual = _normalize_text(answer, criterion.normalize)
            if actual == expected:
                return CriterionAssessment(
                    criterion=label,
                    kind=kind,
                    verdict=CompletionVerdict.PASS,
                    reason="answer exactly matches the required value",
                    evidence_refs=evidence_refs,
                )
            return CriterionAssessment(
                criterion=label,
                kind=kind,
                verdict=CompletionVerdict.FAIL,
                reason="answer does not exactly match the required value",
                evidence_refs=evidence_refs,
            )
        if criterion.kind is CriterionKind.MUST_NOT_INCLUDE:
            if _forbidden_content_present(criterion.value, answer):
                return CriterionAssessment(
                    criterion=label,
                    kind=kind,
                    verdict=CompletionVerdict.FAIL,
                    reason="answer contains forbidden criterion content",
                    evidence_refs=evidence_refs,
                )
            return CriterionAssessment(
                criterion=label,
                kind=kind,
                verdict=CompletionVerdict.PASS,
                reason="answer does not contain forbidden criterion content",
                evidence_refs=evidence_refs,
            )
        # must_include (including legacy bare-string criteria)
        terms = expand_criterion_terms(criterion.value)
        haystack = answer.lower()
        if not terms:
            return CriterionAssessment(
                criterion=label,
                kind=kind,
                verdict=CompletionVerdict.PASS,
                reason="criterion has no deterministic content terms",
                evidence_refs=evidence_refs,
            )
        if any(term in haystack for term in terms):
            return CriterionAssessment(
                criterion=label,
                kind=kind,
                verdict=CompletionVerdict.PASS,
                reason="answer contains a criterion term",
                evidence_refs=evidence_refs,
            )
        return CriterionAssessment(
            criterion=label,
            kind=kind,
            verdict=CompletionVerdict.FAIL,
            reason="answer does not contain a criterion term",
            evidence_refs=evidence_refs,
        )


def _normalize_text(text: str, normalize: tuple[str, ...]) -> str:
    result = text
    if "trim" in normalize:
        result = result.strip()
    if "collapse_whitespace" in normalize:
        result = re.sub(r"\s+", " ", result.strip())
    return result


def _forbidden_content_present(value: str, answer: str) -> bool:
    haystack = answer.lower()
    needle = value.strip().lower()
    if needle and needle in haystack:
        return True
    return any(term in haystack for term in expand_criterion_terms(value))
