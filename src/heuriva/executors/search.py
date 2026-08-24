from __future__ import annotations

from heuriva.clients.search import SearchClient
from heuriva.core.decision import Decision, SearchParams
from heuriva.core.observation import ErrorInfo
from heuriva.core.state import CognitiveState, EvidenceItem, FailureRecord
from heuriva.core.state_patch import OperationResult, StatePatch


class SearchExecutor:
    def __init__(self, *, search_client: SearchClient) -> None:
        self.search_client = search_client

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
        evidence = tuple(
            EvidenceItem(
                content=f"{source.title}: {source.snippet}".strip(),
                source_type="search",
                source_ref=source.url,
                retrieved_at=source.retrieved_at,
            )
            for source in sources
        )
        content = "\n".join(f"{source.rank}. {source.title} - {source.url}" for source in sources)
        return OperationResult(
            content=content,
            patch=StatePatch(
                evidence_add=evidence, confidence=max(state.confidence, decision.confidence)
            ),
            citations=sources,
            metadata={"result_count": len(sources)},
        )
