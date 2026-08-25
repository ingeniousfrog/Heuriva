"""Read-only query / DTO layer for the local trajectory browser."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from heuriva.evaluation import evaluate_trajectory
from heuriva.storage.sqlite import SQLiteStore

GOAL_SUMMARY_MAX = 120
OBSERVATION_SUMMARY_MAX = 240


@dataclass(frozen=True)
class TaskListItem:
    task_id: str
    goal: str
    goal_summary: str
    status: str
    created_at: str
    updated_at: str
    completed_at: str | None
    step_count: int
    termination_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StepSummary:
    step_index: int
    operator: str
    objective: str
    reason: str
    observation_summary: str
    observation_status: str
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvalRunSummary:
    eval_run_id: str
    created_at: str
    judge_mode: str
    deterministic_verdict: str
    judge_verdict: str
    disagreement_bucket: str
    model: str
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskDetail:
    task_id: str
    goal: str
    status: str
    created_at: str | None
    updated_at: str | None
    completed_at: str | None
    final_answer: str | None
    termination_reason: str | None
    task_contract: dict[str, Any] | None
    citation_validation: str
    completion_verdict: str
    completion_assessment: dict[str, Any] | None
    failed_criteria: tuple[str, ...]
    steps: tuple[StepSummary, ...]
    eval_runs: tuple[EvalRunSummary, ...]
    disclaimer: str = (
        "Local read-only inspector. Does not call the model, rewrite trajectories, "
        "or change quality defaults. Citation status is displayed from stored "
        "validation; VERIFY remains a separate gated design."
    )
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        payload["eval_runs"] = [run.to_dict() for run in self.eval_runs]
        payload["failed_criteria"] = list(self.failed_criteria)
        return payload


def summarize_goal(goal: str, *, max_chars: int = GOAL_SUMMARY_MAX) -> str:
    text = " ".join(goal.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def summarize_observation(content: str, *, max_chars: int = OBSERVATION_SUMMARY_MAX) -> str:
    text = " ".join(str(content or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def trajectory_steps_fingerprint(data: dict[str, Any]) -> tuple[Any, ...]:
    """Stable fingerprint of stored steps for read-only assertions."""
    steps = data.get("steps") or ()
    return tuple(
        (
            step.get("step_index"),
            (step.get("decision") or {}).get("id"),
            (step.get("observation") or {}).get("id"),
            (step.get("observation") or {}).get("metadata"),
        )
        for step in steps
    )


def extract_task_contract(data: dict[str, Any]) -> dict[str, Any] | None:
    steps = data.get("steps") or ()
    if not steps:
        return None
    for candidate in (
        steps[-1].get("state_after"),
        steps[-1].get("state_before"),
        steps[0].get("state_before"),
    ):
        if isinstance(candidate, dict) and isinstance(candidate.get("task_contract"), dict):
            return dict(candidate["task_contract"])
    return None


def extract_completion_assessment(data: dict[str, Any]) -> dict[str, Any] | None:
    assessment: dict[str, Any] | None = None
    for step in data.get("steps") or ():
        metadata = (step.get("observation") or {}).get("metadata") or {}
        raw = metadata.get("completion_assessment")
        if isinstance(raw, dict):
            assessment = dict(raw)
    return assessment


class TrajectoryBrowser:
    """Assembles list/detail DTOs from SQLite without writing trajectory steps."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def list_tasks(self, *, limit: int = 100, offset: int = 0) -> tuple[TaskListItem, ...]:
        rows = self.store.list_tasks(limit=limit, offset=offset)
        items: list[TaskListItem] = []
        for row in rows:
            goal = str(row.get("goal") or "")
            items.append(
                TaskListItem(
                    task_id=str(row["task_id"]),
                    goal=goal,
                    goal_summary=summarize_goal(goal),
                    status=str(row.get("status") or "unknown"),
                    created_at=str(row.get("created_at") or ""),
                    updated_at=str(row.get("updated_at") or ""),
                    completed_at=(
                        None if row.get("completed_at") is None else str(row["completed_at"])
                    ),
                    step_count=int(row.get("step_count") or 0),
                    termination_reason=(
                        None
                        if row.get("termination_reason") is None
                        else str(row["termination_reason"])
                    ),
                )
            )
        return tuple(items)

    def get_task(self, task_id: str) -> TaskDetail:
        data = self.store.get_trajectory(task_id)
        report = evaluate_trajectory(data)
        trajectory = data["trajectory"]
        assessment = extract_completion_assessment(data)
        steps = tuple(
            StepSummary(
                step_index=int(step.get("step_index") or 0),
                operator=str((step.get("decision") or {}).get("operator") or ""),
                objective=str((step.get("decision") or {}).get("objective") or ""),
                reason=str((step.get("decision") or {}).get("reason") or ""),
                observation_summary=summarize_observation(
                    str((step.get("observation") or {}).get("content") or "")
                ),
                observation_status=str((step.get("observation") or {}).get("status") or ""),
                created_at=(
                    None if step.get("created_at") is None else str(step.get("created_at"))
                ),
            )
            for step in data.get("steps") or ()
        )
        eval_runs = tuple(
            EvalRunSummary(
                eval_run_id=str(run["id"]),
                created_at=str(run["created_at"]),
                judge_mode=str(run["judge_mode"]),
                deterministic_verdict=str(run["deterministic_verdict"]),
                judge_verdict=str(run["judge_verdict"]),
                disagreement_bucket=str(run["disagreement_bucket"]),
                model=str(run["model"]),
                failure_code=(
                    None if run.get("failure_code") is None else str(run["failure_code"])
                ),
            )
            for run in reversed(self.store.list_eval_runs(task_id))
        )
        try:
            task_meta = self.store.get_task_summary(task_id)
        except KeyError:
            task_meta = {}
        return TaskDetail(
            task_id=str(trajectory["task_id"]),
            goal=str(task_meta.get("goal") or ""),
            status=str(trajectory.get("status") or task_meta.get("status") or "unknown"),
            created_at=(
                None if task_meta.get("created_at") is None else str(task_meta["created_at"])
            ),
            updated_at=(
                None if task_meta.get("updated_at") is None else str(task_meta["updated_at"])
            ),
            completed_at=(
                None if task_meta.get("completed_at") is None else str(task_meta["completed_at"])
            ),
            final_answer=(
                None
                if trajectory.get("final_answer") is None
                else str(trajectory.get("final_answer"))
            ),
            termination_reason=(
                None
                if trajectory.get("termination_reason") is None
                else str(trajectory.get("termination_reason"))
            ),
            task_contract=extract_task_contract(data),
            citation_validation=report.citation_validation,
            completion_verdict=report.completion_verdict,
            completion_assessment=assessment,
            failed_criteria=report.failed_criteria,
            steps=steps,
            eval_runs=eval_runs,
        )
