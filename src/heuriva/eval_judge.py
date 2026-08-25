from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from heuriva.clients.model import ModelChatResult, ModelClientError
from heuriva.core.common import utc_now
from heuriva.core.evaluation import (
    CompletionVerdict,
    JudgeAssessment,
    JudgeProvenance,
    JudgeVerdict,
)
from heuriva.runtime.structured_output import parse_json_object

JUDGE_PROMPT_VERSION = "v0.5.0"
JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.txt"
DEFAULT_MAX_JUDGE_CALLS = 3


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> ModelChatResult: ...


class DeterministicSignals(Protocol):
    @property
    def task_id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def completion_verdict(self) -> str: ...

    @property
    def failed_criteria(self) -> tuple[str, ...]: ...

    @property
    def citation_validation(self) -> str: ...

    @property
    def accepted_evidence_count(self) -> int: ...

    @property
    def rejected_candidate_count(self) -> int: ...


class FreshJudge:
    """Opt-in model judge. Never rewrites trajectories; failures are not passes."""

    def __init__(
        self,
        *,
        model_client: ChatModel,
        model_name: str,
        base_url: str,
        repair_attempts: int = 1,
        max_calls: int = DEFAULT_MAX_JUDGE_CALLS,
        prompt_template: str | None = None,
        prompt_version: str = JUDGE_PROMPT_VERSION,
    ) -> None:
        self.model_client = model_client
        self.model_name = model_name
        self.base_url = base_url
        self.repair_attempts = max(0, repair_attempts)
        self.max_calls = max(1, max_calls)
        self.prompt_template = prompt_template or JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
        self.prompt_version = prompt_version
        self.prompt_hash = hashlib.sha256(self.prompt_template.encode("utf-8")).hexdigest()[:16]

    def judge(
        self,
        *,
        trajectory_data: dict[str, Any],
        deterministic: DeterministicSignals,
        case_id: str | None = None,
    ) -> JudgeAssessment:
        payload = build_judge_payload(trajectory_data, deterministic)
        trajectory = trajectory_data.get("trajectory", {})
        task_id = str(trajectory.get("task_id") or deterministic.task_id)
        trajectory_id = None if trajectory.get("id") is None else str(trajectory.get("id"))
        validation_error = ""
        call_count = 0
        last_attempt_count = 1
        last_error = "judge did not return valid JSON"
        last_failure_code = "parse_failure"

        for _attempt in range(self.repair_attempts + 1):
            if call_count >= self.max_calls:
                break
            messages = self._messages(payload=payload, validation_error=validation_error)
            try:
                response = self.model_client.chat(messages)
                call_count += 1
                last_attempt_count = int(response.metadata.get("attempt_count") or 1)
                parsed = parse_json_object(response.content, phase="judge")
                assessment = _validate_judge_payload(parsed)
                return JudgeAssessment(
                    verdict=assessment["verdict"],
                    reason=assessment["reason"],
                    failed_criteria=assessment["failed_criteria"],
                    manual_review_needed=assessment["manual_review_needed"],
                    provenance=self._provenance(
                        task_id=task_id,
                        trajectory_id=trajectory_id,
                        case_id=case_id,
                        attempt_count=last_attempt_count,
                        call_count=call_count,
                        failure_code=None,
                    ),
                )
            except ModelClientError as exc:
                call_count += 1
                last_attempt_count = exc.attempt_count
                last_error = exc.message
                last_failure_code = exc.code
                return JudgeAssessment(
                    verdict=JudgeVerdict.ERROR,
                    reason=f"judge model call failed: {exc.code}: {exc.message}",
                    failed_criteria=(),
                    manual_review_needed=True,
                    provenance=self._provenance(
                        task_id=task_id,
                        trajectory_id=trajectory_id,
                        case_id=case_id,
                        attempt_count=last_attempt_count,
                        call_count=call_count,
                        failure_code=exc.code,
                    ),
                )
            except (json.JSONDecodeError, ValidationError, ValueError, TypeError, KeyError) as exc:
                validation_error = str(exc)
                last_error = validation_error
                last_failure_code = "parse_failure"

        return JudgeAssessment(
            verdict=JudgeVerdict.PARSE_FAILURE,
            reason=f"judge parse failure after {call_count} call(s): {last_error}",
            failed_criteria=(),
            manual_review_needed=True,
            provenance=self._provenance(
                task_id=task_id,
                trajectory_id=trajectory_id,
                case_id=case_id,
                attempt_count=last_attempt_count,
                call_count=call_count,
                failure_code=last_failure_code,
            ),
        )

    def _provenance(
        self,
        *,
        task_id: str,
        trajectory_id: str | None,
        case_id: str | None,
        attempt_count: int,
        call_count: int,
        failure_code: str | None,
    ) -> JudgeProvenance:
        return JudgeProvenance(
            model=self.model_name,
            base_url=self.base_url,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            timestamp=utc_now().isoformat(),
            task_id=task_id,
            trajectory_id=trajectory_id,
            case_id=case_id,
            attempt_count=max(1, attempt_count),
            call_count=call_count,
            failure_code=failure_code,
        )

    def _messages(self, *, payload: dict[str, Any], validation_error: str) -> list[dict[str, str]]:
        body = dict(payload)
        body["validation_error"] = validation_error
        return [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
        ]


def _format_criterion_for_judge(item: object) -> str | None:
    if isinstance(item, dict):
        kind = str(item.get("kind") or "").strip()
        value = str(item.get("value") or "").strip()
        if kind and value:
            return f"{kind}:{value}"
        if value:
            return value
        return None
    text = str(item).strip()
    return text or None


def build_judge_payload(
    trajectory_data: dict[str, Any],
    deterministic: DeterministicSignals,
) -> dict[str, Any]:
    trajectory = trajectory_data.get("trajectory", {})
    steps = trajectory_data.get("steps", ())
    goal = ""
    criteria: list[str] = []
    evidence_summary: list[dict[str, Any]] = []
    if steps:
        first_before = steps[0].get("state_before", {})
        goal = str(first_before.get("goal") or "")
        contract = first_before.get("task_contract") or {}
        raw_criteria = contract.get("criteria") or ()
        if isinstance(raw_criteria, (list, tuple)):
            for item in raw_criteria:
                formatted = _format_criterion_for_judge(item)
                if formatted:
                    criteria.append(formatted)
        evidence = first_before.get("evidence") or ()
        # Prefer final state evidence if present.
        last_after = steps[-1].get("state_after", {})
        evidence = last_after.get("evidence") or evidence
        if isinstance(evidence, (list, tuple)):
            for item in evidence[:12]:
                if not isinstance(item, dict):
                    continue
                evidence_summary.append(
                    {
                        "id": item.get("id"),
                        "source": item.get("source"),
                        "snippet": str(item.get("snippet") or "")[:400],
                    }
                )
    return {
        "goal": goal,
        "criteria": criteria,
        "final_answer": trajectory.get("final_answer"),
        "task_status": trajectory.get("status"),
        "termination_reason": trajectory.get("termination_reason"),
        "evidence_summary": evidence_summary,
        "deterministic": {
            "completion_verdict": deterministic.completion_verdict,
            "failed_criteria": list(deterministic.failed_criteria),
            "citation_validation": deterministic.citation_validation,
            "accepted_evidence_count": deterministic.accepted_evidence_count,
            "rejected_candidate_count": deterministic.rejected_candidate_count,
            "status": deterministic.status,
        },
        "allowed_verdicts": [
            CompletionVerdict.PASS.value,
            CompletionVerdict.FAIL.value,
            CompletionVerdict.INSUFFICIENT_EVIDENCE.value,
        ],
    }


def _validate_judge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_verdict = str(payload.get("verdict") or "").strip().lower()
    try:
        verdict = JudgeVerdict(raw_verdict)
    except ValueError as exc:
        raise ValueError("judge verdict must be pass, fail, or insufficient_evidence") from exc
    if verdict not in {
        JudgeVerdict.PASS,
        JudgeVerdict.FAIL,
        JudgeVerdict.INSUFFICIENT_EVIDENCE,
    }:
        raise ValueError("judge verdict must be pass, fail, or insufficient_evidence")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("judge reason must be non-empty")
    raw_failed = payload.get("failed_criteria") or []
    if not isinstance(raw_failed, (list, tuple)):
        raise ValueError("judge failed_criteria must be an array")
    failed_criteria = tuple(str(item).strip() for item in raw_failed if str(item).strip())
    manual = payload.get("manual_review_needed", False)
    if not isinstance(manual, bool):
        raise ValueError("judge manual_review_needed must be a boolean")
    if verdict is JudgeVerdict.PASS and failed_criteria:
        raise ValueError("pass verdict cannot include failed_criteria")
    return {
        "verdict": verdict,
        "reason": reason,
        "failed_criteria": failed_criteria,
        "manual_review_needed": manual,
    }
