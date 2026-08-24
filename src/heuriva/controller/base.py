from __future__ import annotations

from typing import Protocol

from heuriva.core.decision import Decision
from heuriva.core.event import RuntimeEvent
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState


class Controller(Protocol):
    def select(
        self,
        *,
        state: CognitiveState,
        available_operators: tuple[Operator, ...],
        runtime_limits: dict[str, object],
        policy_hints: tuple[str, ...] = (),
    ) -> tuple[Decision, list[RuntimeEvent]]: ...
