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
from heuriva.core.task_contract import EvidenceRequirement, SearchPolicy

STOP_WORDS = {
    "about",
    "answer",
    "describe",
    "explain",
    "include",
    "mention",
    "must",
    "need",
    "needs",
    "provide",
    "show",
    "summarize",
    "the",
    "this",
    "with",
}

TERM_EQUIVALENTS = {
    "safety": (
        "safety",
        "safe",
        "risk",
        "risks",
        "安全",
        "风险",
        "伤害",
        "未成年",
        "合规",
        "红线",
    ),
    "tradeoff": (
        "tradeoff",
        "tradeoffs",
        "trade-off",
        "trade-offs",
        "权衡",
        "取舍",
        "代价",
        "成本",
        "损失",
        "摩擦",
    ),
    "tradeoffs": (
        "tradeoffs",
        "tradeoff",
        "trade-offs",
        "trade-off",
        "权衡",
        "取舍",
        "代价",
        "成本",
        "损失",
        "摩擦",
    ),
}


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
        if not answer:
            return CompletionAssessment(
                verdict=CompletionVerdict.FAIL,
                failed_criteria=contract.criteria,
                feedback="answer was empty",
            )
        if (
            contract.evidence_requirement is EvidenceRequirement.REQUIRED
            or contract.search_policy is SearchPolicy.REQUIRED
        ) and not state.evidence:
            return CompletionAssessment(
                verdict=CompletionVerdict.INSUFFICIENT_EVIDENCE,
                failed_criteria=contract.criteria,
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
        criterion: str,
        answer: str,
        state: CognitiveState,
    ) -> CriterionAssessment:
        terms = _criterion_terms(criterion)
        haystack = answer.lower()
        if not terms:
            return CriterionAssessment(
                criterion=criterion,
                verdict=CompletionVerdict.PASS,
                reason="criterion has no deterministic content terms",
                evidence_refs=tuple(item.id for item in state.evidence),
            )
        if any(term in haystack for term in terms):
            return CriterionAssessment(
                criterion=criterion,
                verdict=CompletionVerdict.PASS,
                reason="answer contains a criterion term",
                evidence_refs=tuple(item.id for item in state.evidence),
            )
        return CriterionAssessment(
            criterion=criterion,
            verdict=CompletionVerdict.FAIL,
            reason="answer does not contain a criterion term",
        )


def _criterion_terms(criterion: str) -> tuple[str, ...]:
    terms: list[str] = []
    for term in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}", criterion.lower()):
        if term in STOP_WORDS:
            continue
        for expanded in TERM_EQUIVALENTS.get(term, (term,)):
            if expanded not in terms:
                terms.append(expanded)
    return tuple(terms)
