from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from heuriva.core.common import utc_now
from heuriva.core.observation import ErrorInfo, SourceRef


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    snippet: str = ""
    rank: int = Field(ge=1)

    def to_source_ref(self) -> SourceRef:
        return SourceRef(
            title=self.title,
            url=self.url,
            snippet=self.snippet,
            rank=self.rank,
            retrieved_at=utc_now(),
        )


class SearchProvider(Protocol):
    def text(self, query: str, *, max_results: int) -> list[dict[str, object]]: ...


class SearchClient:
    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: float = 15.0,
        provider: SearchProvider | None = None,
    ) -> None:
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.provider = provider

    def search(self, query: str) -> tuple[tuple[SearchResult, ...], ErrorInfo | None]:
        if not query.strip():
            return (), ErrorInfo(
                code="empty_query", message="search query was empty", retryable=False
            )
        try:
            provider = self.provider or self._default_provider(self.timeout_seconds)
            raw_results = provider.text(query, max_results=self.max_results)
        except TimeoutError:
            return (), ErrorInfo(
                code="search_timeout",
                message=f"search exceeded {self.timeout_seconds:g}s timeout",
                retryable=True,
            )
        except Exception as exc:  # ddgs surfaces several provider-specific exception types.
            if "timeout" in exc.__class__.__name__.lower():
                return (), ErrorInfo(
                    code="search_timeout",
                    message=f"search exceeded {self.timeout_seconds:g}s timeout",
                    retryable=True,
                )
            return (), ErrorInfo(
                code="search_error", message=exc.__class__.__name__, retryable=True
            )
        results: list[SearchResult] = []
        for rank, item in enumerate(raw_results[: self.max_results], start=1):
            title = str(item.get("title") or item.get("heading") or "").strip()
            url = str(item.get("href") or item.get("url") or "").strip()
            snippet = str(item.get("body") or item.get("snippet") or "").strip()
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet=snippet, rank=rank))
        if not results:
            return (), ErrorInfo(
                code="no_results", message="search returned no usable results", retryable=True
            )
        return tuple(results), None

    @staticmethod
    def _default_provider(timeout_seconds: float) -> SearchProvider:
        from ddgs import DDGS

        return DDGS(timeout=max(1, int(timeout_seconds)))
