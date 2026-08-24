from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from heuriva.clients.model import ModelChatResult, ModelClientError
from heuriva.core.decision import (
    Decision,
    DecisionDraft,
    bind_decision,
    normalize_draft_payload,
)
from heuriva.core.event import EventLevel, RuntimeEvent
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.redaction import redact_text

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "controller.txt"


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> ModelChatResult: ...


class LLMController:
    def __init__(
        self,
        *,
        model_client: ChatModel,
        repair_attempts: int = 1,
        prompt_template: str | None = None,
    ) -> None:
        self.model_client = model_client
        self.repair_attempts = repair_attempts
        self.prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")
        self.prompt_hash = hashlib.sha256(self.prompt_template.encode("utf-8")).hexdigest()[:16]

    def select(
        self,
        *,
        state: CognitiveState,
        available_operators: tuple[Operator, ...],
        runtime_limits: dict[str, object],
        policy_hints: tuple[str, ...] = (),
    ) -> tuple[Decision, list[RuntimeEvent]]:
        events: list[RuntimeEvent] = []
        validation_error = ""
        for attempt in range(self.repair_attempts + 1):
            messages = self._messages(
                state=state,
                available_operators=available_operators,
                runtime_limits=runtime_limits,
                policy_hints=policy_hints,
                validation_error=validation_error if attempt else "",
            )
            try:
                response = self.model_client.chat(messages)
                payload = self._parse_json_object(response.content)
                draft = DecisionDraft.model_validate(normalize_draft_payload(payload))
                if draft.operator not in available_operators:
                    raise ValueError(f"operator {draft.operator.value} is not available")
                return bind_decision(draft, state), events
            except (json.JSONDecodeError, ValidationError, ValueError, ModelClientError) as exc:
                validation_error = str(exc)
                events.append(
                    RuntimeEvent(
                        task_id=state.task_id,
                        step_index=state.step_index,
                        event_type="controller_parse_error",
                        level=EventLevel.WARNING,
                        payload={
                            "attempt": attempt,
                            "error": redact_text(validation_error),
                        },
                    )
                )
        raise ValueError("Controller did not return valid JSON")

    def _messages(
        self,
        *,
        state: CognitiveState,
        available_operators: tuple[Operator, ...],
        runtime_limits: dict[str, object],
        policy_hints: tuple[str, ...],
        validation_error: str,
    ) -> list[dict[str, str]]:
        payload = {
            "state": state.model_dump(mode="json"),
            "available_operators": [operator.value for operator in available_operators],
            "runtime_limits": runtime_limits,
            "policy_hints": policy_hints,
            "validation_error": validation_error,
        }
        return [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                stripped = "\n".join(lines[1:-1]).strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("controller response must be a JSON object")
        return payload
