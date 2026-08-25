from __future__ import annotations

from typing import Protocol

from heuriva.clients.search import SearchResult
from heuriva.config import QualityConfig
from heuriva.core.decision import Decision, SearchParams
from heuriva.core.observation import ErrorInfo
from heuriva.core.state import CognitiveState, FailureRecord
from heuriva.core.state_patch import OperationResult, StatePatch
from heuriva.runtime.evidence_relevance import assess_search_candidates


class SearchClientLike(Protocol):
    def search(self, query: str) -> tuple[tuple[SearchResult, ...], ErrorInfo | None]: ...


class SearchExecutor:
    def __init__(
        self,
        *,
        search_client: SearchClientLike,
        quality_config: QualityConfig | None = None,
    ) -> None:
        self.search_client = search_client
        self.quality_config = quality_config or QualityConfig()

    def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
        if not isinstance(decision.params, SearchParams):
            invalid_error = ErrorInfo(code="invalid_search_params", message="SEARCH requires query")
            return OperationResult(error=invalid_error)
        results, error = self.search_client.search(decision.params.query)
        if error is not None:
            return OperationResult(
                error=error,
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
            )
        sources = tuple(result.to_source_ref() for result in results)
        assessed = assess_search_candidates(
            state=state,
            decision=decision,
            sources=sources,
            quality=self.quality_config,
        )
        content = "\n".join(f"{source.rank}. {source.title} - {source.url}" for source in sources)
        return OperationResult(
            content=content,
            patch=StatePatch(
                evidence_add=assessed.accepted_evidence,
                confidence=max(state.confidence, decision.confidence)
                if assessed.accepted_evidence
                else state.confidence,
            ),
            citations=assessed.accepted_sources,
            metadata={
                "result_count": len(sources),
                "raw_candidate_count": assessed.raw_candidate_count,
                "accepted_evidence_count": assessed.accepted_evidence_count,
                "rejected_candidate_count": assessed.rejected_candidate_count,
                "candidate_assessments": [
                    assessment.model_dump(mode="json") for assessment in assessed.assessments
                ],
                "evidence_relevance_mode": self.quality_config.evidence_relevance_mode.value,
            },
        )
