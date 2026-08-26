from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from heuriva.core.state import StateStatus

RESUMABLE_STATUSES = frozenset(
    {
        StateStatus.RUNNING.value,
        StateStatus.INTERRUPTED.value,
        StateStatus.FAILED.value,
        StateStatus.MAX_STEPS_REACHED.value,
    }
)


@dataclass(frozen=True)
class ResumeEligibility:
    eligible: bool
    reason: str
    task_id: str
    task_status: str
    step_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "task_id": self.task_id,
            "task_status": self.task_status,
            "step_count": self.step_count,
            "warnings": list(self.warnings),
        }


def assess_resume_eligibility(
    *,
    task_id: str,
    task_status: str,
    step_count: int,
    force: bool = False,
    has_latest_state: bool = True,
    config_warnings: tuple[str, ...] = (),
) -> ResumeEligibility:
    warnings = list(config_warnings)
    if not has_latest_state:
        return ResumeEligibility(
            eligible=False,
            reason="missing_state",
            task_id=task_id,
            task_status=task_status,
            step_count=step_count,
            warnings=tuple(warnings),
        )
    if task_status == StateStatus.DONE.value:
        if force:
            warnings.append("force_resume_done_task")
            return ResumeEligibility(
                eligible=True,
                reason="forced_done",
                task_id=task_id,
                task_status=task_status,
                step_count=step_count,
                warnings=tuple(warnings),
            )
        return ResumeEligibility(
            eligible=False,
            reason="already_done",
            task_id=task_id,
            task_status=task_status,
            step_count=step_count,
            warnings=tuple(warnings),
        )
    if task_status not in RESUMABLE_STATUSES:
        return ResumeEligibility(
            eligible=False,
            reason="unsupported_status",
            task_id=task_id,
            task_status=task_status,
            step_count=step_count,
            warnings=tuple(warnings),
        )
    return ResumeEligibility(
        eligible=True,
        reason="ok",
        task_id=task_id,
        task_status=task_status,
        step_count=step_count,
        warnings=tuple(warnings),
    )


def config_snapshot_warnings(
    stored: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[str, ...]:
    if not stored:
        return ("missing_stored_config_snapshot",)
    warnings: list[str] = []
    stored_llm_raw = stored.get("llm")
    current_llm_raw = current.get("llm")
    stored_llm: dict[str, Any] = stored_llm_raw if isinstance(stored_llm_raw, dict) else {}
    current_llm: dict[str, Any] = current_llm_raw if isinstance(current_llm_raw, dict) else {}
    for key in ("base_url", "model"):
        if stored_llm.get(key) != current_llm.get(key):
            warnings.append(f"config_drift:llm.{key}")
    return tuple(warnings)
