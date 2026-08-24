from __future__ import annotations

import json
from typing import Any

from heuriva.clients.model import ModelChatResult
from heuriva.core.decision import (
    AnalyzeParams,
    AnswerParams,
    Decision,
    DecisionDraft,
    SearchParams,
    bind_decision,
)
from heuriva.core.event import RuntimeEvent
from heuriva.core.observation import SourceRef
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState, EvidenceItem, KnownItem
from heuriva.core.state_patch import OperationResult, StatePatch


class QueueModelClient:
    def __init__(self, responses: list[str | dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("QueueModelClient has no responses left")
        response = self.responses.pop(0)
        content = json.dumps(response) if isinstance(response, dict) else response
        return ModelChatResult(content=content, metadata={"model": "fake"})


class FakeController:
    def __init__(self, drafts: list[DecisionDraft]) -> None:
        self.drafts = list(drafts)
        self.available_history: list[tuple[Operator, ...]] = []

    def select(
        self,
        *,
        state: CognitiveState,
        available_operators: tuple[Operator, ...],
        runtime_limits: dict[str, object],
        policy_hints: tuple[str, ...] = (),
    ) -> tuple[Decision, list[RuntimeEvent]]:
        del runtime_limits, policy_hints
        self.available_history.append(available_operators)
        if not self.drafts:
            raise ValueError("fake controller exhausted")
        draft = self.drafts.pop(0)
        if draft.operator not in available_operators:
            raise ValueError(f"operator {draft.operator.value} not available")
        return bind_decision(draft, state), []


class FakeExecutor:
    def __init__(
        self,
        content: str,
        *,
        known: str | None = None,
        evidence_url: str | None = None,
        final_answer: str | None = None,
    ) -> None:
        self.content = content
        self.known = known
        self.evidence_url = evidence_url
        self.final_answer = final_answer
        self.calls = 0

    def execute(self, decision: Decision, state: CognitiveState) -> OperationResult:
        del decision
        self.calls += 1
        evidence: tuple[EvidenceItem, ...] = ()
        citations: tuple[SourceRef, ...] = ()
        if self.evidence_url:
            source = SourceRef(title="Source", url=self.evidence_url, snippet="Fake source", rank=1)
            citations = (source,)
            evidence = (
                EvidenceItem(
                    content="Fake source",
                    source_type="search",
                    source_ref=self.evidence_url,
                    retrieved_at=source.retrieved_at,
                ),
            )
        known = (
            (KnownItem(content=self.known, origin="task_input"),) if self.known is not None else ()
        )
        patch = StatePatch(
            known_add=known,
            evidence_add=evidence,
            confidence=max(state.confidence, 0.5),
        )
        return OperationResult(
            content=self.content,
            patch=patch,
            citations=citations,
            final_answer=self.final_answer,
        )


def make_analyze_decision(objective: str) -> DecisionDraft:
    return DecisionDraft(
        operator=Operator.ANALYZE,
        objective=objective,
        reason="need to inspect the task",
        success_criteria=("analysis produced",),
        params=AnalyzeParams(focus="task"),
        confidence=0.5,
    )


def make_search_decision(query: str) -> DecisionDraft:
    return DecisionDraft(
        operator=Operator.SEARCH,
        objective="Find supporting evidence",
        reason="need outside source metadata",
        success_criteria=("source with URL",),
        params=SearchParams(query=query),
        confidence=0.5,
    )


def make_answer_decision(objective: str) -> DecisionDraft:
    return DecisionDraft(
        operator=Operator.ANSWER,
        objective=objective,
        reason="ready to answer",
        success_criteria=("non-empty final answer",),
        params=AnswerParams(),
        confidence=0.8,
    )
