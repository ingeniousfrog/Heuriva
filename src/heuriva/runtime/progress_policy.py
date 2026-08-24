from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from heuriva.core.decision import Decision
from heuriva.core.observation import Observation
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.runtime.state_delta import StateDelta, calculate_state_delta


class ProgressStep(Protocol):
    @property
    def decision(self) -> Decision: ...

    @property
    def observation(self) -> Observation: ...

    @property
    def state(self) -> CognitiveState: ...

    @property
    def state_before(self) -> CognitiveState | None: ...

    @property
    def state_delta(self) -> StateDelta | None: ...


@dataclass(frozen=True)
class ProgressPolicyResult:
    available_operators: tuple[Operator, ...]
    policy_hints: tuple[str, ...] = ()
    guard_action: str | None = None
    guard_reason: str | None = None
    operator_streak: int = 0
    no_progress_steps: int = 0
    remaining_steps: int = 0

    def event_payload(self) -> dict[str, object]:
        return {
            "reason": self.guard_reason,
            "operator_streak": self.operator_streak,
            "no_progress_steps": self.no_progress_steps,
            "remaining_steps": self.remaining_steps,
            "next_available_operators": [operator.value for operator in self.available_operators],
        }


def evaluate_progress_policy(
    *,
    state: CognitiveState,
    committed_steps: Sequence[ProgressStep],
    base_available: tuple[Operator, ...],
    max_steps: int,
    max_same_operator_streak: int,
    max_no_progress_steps: int,
    answer_reserve_steps: int,
) -> ProgressPolicyResult:
    remaining_steps = max(0, max_steps - state.step_index)
    last_operator, operator_streak = _same_operator_streak(committed_steps)
    no_progress_steps = _trailing_no_progress_steps(committed_steps)
    if (
        max_no_progress_steps > 0
        and no_progress_steps >= max_no_progress_steps
        and Operator.ANSWER in base_available
    ):
        return _guarded(
            available=(Operator.ANSWER,),
            reason="no_material_progress",
            operator_streak=operator_streak,
            no_progress_steps=no_progress_steps,
            remaining_steps=remaining_steps,
        )
    reserve = min(max_steps, max(1, answer_reserve_steps))
    if remaining_steps <= reserve and Operator.ANSWER in base_available:
        return _guarded(
            available=(Operator.ANSWER,),
            reason="answer_reserve",
            operator_streak=operator_streak,
            no_progress_steps=no_progress_steps,
            remaining_steps=remaining_steps,
        )
    if (
        last_operator is not None
        and max_same_operator_streak > 0
        and operator_streak >= max_same_operator_streak
        and not _last_answer_validation_failed(committed_steps)
    ):
        reduced = tuple(operator for operator in base_available if operator is not last_operator)
        if not reduced and Operator.ANSWER in base_available:
            reduced = (Operator.ANSWER,)
        if reduced:
            return _guarded(
                available=reduced,
                reason="same_operator_streak",
                operator_streak=operator_streak,
                no_progress_steps=no_progress_steps,
                remaining_steps=remaining_steps,
            )
    return ProgressPolicyResult(
        available_operators=base_available,
        operator_streak=operator_streak,
        no_progress_steps=no_progress_steps,
        remaining_steps=remaining_steps,
    )


def _guarded(
    *,
    available: tuple[Operator, ...],
    reason: str,
    operator_streak: int,
    no_progress_steps: int,
    remaining_steps: int,
) -> ProgressPolicyResult:
    return ProgressPolicyResult(
        available_operators=available,
        policy_hints=(
            f"loop_guard:{reason}; next operators: "
            f"{', '.join(operator.value for operator in available)}",
        ),
        guard_action="restrict_operators",
        guard_reason=reason,
        operator_streak=operator_streak,
        no_progress_steps=no_progress_steps,
        remaining_steps=remaining_steps,
    )


def _same_operator_streak(steps: Sequence[ProgressStep]) -> tuple[Operator | None, int]:
    if not steps:
        return None, 0
    last_operator = steps[-1].decision.operator
    count = 0
    for step in reversed(steps):
        if step.decision.operator is not last_operator:
            break
        count += 1
    return last_operator, count


def _trailing_no_progress_steps(steps: Sequence[ProgressStep]) -> int:
    count = 0
    for step in reversed(steps):
        delta = step.state_delta
        if delta is None and step.state_before is not None:
            delta = calculate_state_delta(step.state_before, step.state)
        if delta is None or delta.material_progress:
            break
        count += 1
    return count


def _last_answer_validation_failed(steps: Sequence[ProgressStep]) -> bool:
    if not steps:
        return False
    latest = steps[-1]
    if latest.decision.operator is not Operator.ANSWER:
        return False
    error = latest.observation.error
    return error is not None and error.code == "answer_validation_error"
