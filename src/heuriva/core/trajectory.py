from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heuriva.core.common import new_id, non_empty, utc_now


class TrajectoryStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    MAX_STEPS_REACHED = "max_steps_reached"
    INTERRUPTED = "interrupted"


class Trajectory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    task_id: str
    initial_state_id: str
    final_state_id: str | None = None
    final_answer: str | None = None
    status: TrajectoryStatus = TrajectoryStatus.RUNNING
    termination_reason: str = "running"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "initial_state_id", "termination_reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return non_empty(value, "trajectory field")
