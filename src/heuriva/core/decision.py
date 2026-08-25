from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from heuriva.core.common import new_id, non_empty, utc_now
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState


class AnalyzeParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    focus: str

    @field_validator("focus")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "analysis focus")


class SearchParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str

    @field_validator("query")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "search query")


class AnswerParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


DecisionParams = AnalyzeParams | SearchParams | AnswerParams


class DecisionDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operator: Operator
    objective: str
    reason: str
    success_criteria: tuple[str, ...]
    params: DecisionParams = Field(default_factory=AnswerParams)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("objective", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "decision field")

    @field_validator("success_criteria")
    @classmethod
    def _criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(non_empty(value, "success criterion") for value in values)
        if not cleaned:
            raise ValueError("success_criteria must contain at least one item")
        return cleaned

    @model_validator(mode="after")
    def _operator_params_match(self) -> DecisionDraft:
        if self.operator is Operator.ANALYZE and not isinstance(self.params, AnalyzeParams):
            raise ValueError("ANALYZE requires AnalyzeParams")
        if self.operator is Operator.SEARCH and not isinstance(self.params, SearchParams):
            raise ValueError("SEARCH requires SearchParams")
        if self.operator is Operator.ANSWER and not isinstance(self.params, AnswerParams):
            raise ValueError("ANSWER does not accept free params")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    task_id: str
    state_id: str
    step_index: int = Field(ge=0)
    operator: Operator
    objective: str
    reason: str
    success_criteria: tuple[str, ...]
    params: DecisionParams
    confidence: float = Field(ge=0.0, le=1.0)
    policy_refs: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id", "state_id", "objective", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "decision field")


def normalize_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = dict(payload)
    criteria = copied.get("success_criteria")
    if isinstance(criteria, str):
        copied["success_criteria"] = [criteria]
    raw_operator = copied.get("operator")
    if not isinstance(raw_operator, str):
        raise ValueError("operator must be a string")
    operator = Operator(raw_operator)
    params = copied.get("params") or {}
    if operator is Operator.ANALYZE:
        copied["params"] = AnalyzeParams.model_validate(params)
    elif operator is Operator.SEARCH:
        copied["params"] = SearchParams.model_validate(params)
    else:
        copied["params"] = AnswerParams.model_validate(params)
    return copied


def bind_decision(draft: DecisionDraft, state: CognitiveState) -> Decision:
    return Decision(
        task_id=state.task_id,
        state_id=state.id,
        step_index=state.step_index,
        operator=draft.operator,
        objective=draft.objective,
        reason=draft.reason,
        success_criteria=draft.success_criteria,
        params=draft.params,
        confidence=draft.confidence,
        policy_refs=(),
    )
