from __future__ import annotations

from heuriva.core.decision import Decision
from heuriva.core.operator import Operator


class ExecutorRouter:
    _mapping = {
        Operator.ANALYZE: "llm",
        Operator.SEARCH: "search",
        Operator.ANSWER: "llm",
    }

    def __init__(self, *, search_enabled: bool = True) -> None:
        self.search_enabled = search_enabled

    def available_operators(self) -> tuple[Operator, ...]:
        if self.search_enabled:
            return (Operator.ANALYZE, Operator.SEARCH, Operator.ANSWER)
        return (Operator.ANALYZE, Operator.ANSWER)

    def resolve(self, decision: Decision) -> str:
        if decision.operator is Operator.SEARCH and not self.search_enabled:
            raise ValueError("SEARCH operator is not available because search is disabled")
        return self._mapping[decision.operator]
