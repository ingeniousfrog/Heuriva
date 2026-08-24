from __future__ import annotations

import json
from typing import Any

from heuriva.core.decision import Decision


def render_step(*, decision: Decision, observation_content: str, trace: bool) -> str:
    base = (
        f"[step {decision.step_index}] {decision.operator.value}: "
        f"{decision.objective} | reason: {decision.reason}"
    )
    if not trace:
        return base
    content = observation_content.strip() or "<empty>"
    return f"{base}\n  observation: {content}"


def render_saved_trajectory(data: dict[str, Any], *, trace: bool) -> str:
    lines = [
        f"task_id: {data['trajectory']['task_id']}",
        f"status: {data['trajectory']['status']}",
    ]
    final = data["trajectory"].get("final_answer")
    if final:
        lines.append(f"final_answer: {final}")
    for step in data["steps"]:
        decision = step["decision"]
        lines.append(
            f"[step {step['step_index']}] {decision['operator']}: "
            f"{decision['objective']} | reason: {decision['reason']}"
        )
        if trace:
            lines.append(f"  observation: {step['observation'].get('content', '')}")
    if trace and data["events"]:
        lines.append("events:")
        for event in data["events"]:
            lines.append(
                f"  {event['level']} {event['event_type']}: {json.dumps(event['payload'])}"
            )
    return "\n".join(lines)
