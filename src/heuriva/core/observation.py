from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import new_id, non_empty, utc_now


class ObservationKind(StrEnum):
    ANALYSIS = "analysis"
    SEARCH_RESULTS = "search_results"
    ANSWER = "answer"
    EXECUTOR_ERROR = "executor_error"


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    snippet: str = ""
    rank: int | None = Field(default=None, ge=1)
    retrieved_at: datetime = Field(default_factory=utc_now)

    @field_validator("title", "url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "source field")


class ErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retryable: bool = False

    @field_validator("code", "message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "error field")


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    task_id: str
    decision_id: str
    kind: ObservationKind
    status: ObservationStatus
    content: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    citations: tuple[SourceRef, ...] = ()
    error: ErrorInfo | None = None
    executor_kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id", "decision_id", "executor_kind")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "observation field")
