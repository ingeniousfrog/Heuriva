from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import new_id, non_empty, utc_now


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    task_id: str
    step_index: int | None = Field(default=None, ge=0)
    event_type: str
    level: EventLevel
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id", "event_type")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "runtime event field")
