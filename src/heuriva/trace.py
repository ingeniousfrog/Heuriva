from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from heuriva.core.decision import Decision
from heuriva.runtime.state_delta import StateDelta


def render_step(
    *,
    decision: Decision,
    observation_content: str,
    trace: bool,
    state_delta: StateDelta | None = None,
) -> str:
    base = (
        f"[step {decision.step_index}] {decision.operator.value}: "
        f"{decision.objective} | reason: {decision.reason}"
    )
    if state_delta is not None:
        base = f"{base} | {state_delta.summary()}"
    if not trace:
        return base
    content = observation_content.strip() or "<empty>"
    lines = [base, f"  observation: {content}"]
    if state_delta is not None:
        lines.extend(f"  {line}" for line in state_delta.detail_lines())
    return "\n".join(lines)


def render_saved_trajectory(data: dict[str, Any], *, trace: bool) -> str:
    lines = [
        f"task_id: {data['trajectory']['task_id']}",
        f"status: {data['trajectory']['status']}",
    ]
    final = data["trajectory"].get("final_answer")
    if final:
        lines.append(f"final_answer: {final}")
    timeline: list[tuple[str, int, dict[str, Any]]] = []
    for step in data["steps"]:
        timeline.append((step.get("created_at", ""), 0, {"kind": "step", "value": step}))
    for event in data["events"]:
        timeline.append((event.get("created_at", ""), 1, {"kind": "event", "value": event}))
    for _created_at, _rank, item in sorted(timeline, key=lambda value: (value[0], value[1])):
        if item["kind"] == "event":
            if trace:
                event = item["value"]
                lines.append(
                    f"event {event['level']} {event['event_type']}: "
                    f"{json.dumps(event['payload'], ensure_ascii=False)}"
                )
            continue
        step = item["value"]
        decision = step["decision"]
        step_line = (
            f"[step {step['step_index']}] {decision['operator']}: "
            f"{decision['objective']} | reason: {decision['reason']}"
        )
        delta = step.get("state_delta")
        if delta:
            step_line = f"{step_line} | {delta['summary']}"
        lines.append(step_line)
        if trace:
            lines.append(f"  observation: {step['observation'].get('content', '')}")
            if delta:
                lines.extend(f"  {detail}" for detail in delta.get("details", ()))
    if trace:
        lines.extend(_summary_lines(data))
    return "\n".join(lines)


def _summary_lines(data: dict[str, Any]) -> list[str]:
    trajectory = data["trajectory"]
    failures = [
        step["observation"]["error"]["code"]
        for step in data["steps"]
        if step["observation"].get("error") is not None
    ]
    return [
        "summary:",
        f"  status: {trajectory['status']}",
        f"  termination_reason: {trajectory.get('termination_reason', '')}",
        f"  step_count: {len(data['steps'])}",
        f"  elapsed_seconds: {_elapsed_seconds(trajectory)}",
        f"  failure_summary: {', '.join(failures) if failures else 'none'}",
        f"  recovery_command: heuriva show --trace {trajectory['task_id']}",
    ]


def _elapsed_seconds(trajectory: dict[str, Any]) -> str:
    started_at = trajectory.get("started_at")
    completed_at = trajectory.get("completed_at")
    if not isinstance(started_at, str) or not isinstance(completed_at, str):
        return "unknown"
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return "unknown"
    seconds = max(0.0, (completed - started).total_seconds())
    return f"{seconds:.3f}".rstrip("0").rstrip(".")
