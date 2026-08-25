from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TrajectoryEvaluationReport:
    task_id: str
    status: str
    evidence_level: str
    search_steps: int
    search_guard_count: int
    duplicate_query_count: int
    raw_candidate_count: int
    accepted_evidence_count: int
    rejected_candidate_count: int
    citation_validation: str
    completion_verdict: str
    failed_criteria: tuple[str, ...]
    parse_warning_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_trajectory(
    data: dict[str, Any], *, evidence_level: str = "deterministic"
) -> TrajectoryEvaluationReport:
    trajectory = data["trajectory"]
    steps = data.get("steps", ())
    events = data.get("events", ())
    search_steps = 0
    raw_candidate_count = 0
    accepted_evidence_count = 0
    rejected_candidate_count = 0
    citation_validation = "not_assessed"
    completion_verdict = "not_assessed"
    failed_criteria: tuple[str, ...] = ()
    for step in steps:
        decision = step.get("decision", {})
        observation = step.get("observation", {})
        metadata = observation.get("metadata", {})
        if decision.get("operator") == "SEARCH":
            search_steps += 1
            raw_candidate_count += _safe_int(metadata.get("raw_candidate_count"))
            accepted_from_metadata = metadata.get("accepted_evidence_count")
            if accepted_from_metadata is None:
                delta = step.get("state_delta", {})
                accepted_evidence_count += _safe_int(delta.get("added_evidence_count"))
            else:
                accepted_evidence_count += _safe_int(accepted_from_metadata)
            rejected_candidate_count += _safe_int(metadata.get("rejected_candidate_count"))
        if metadata.get("citation_validation"):
            citation_validation = str(metadata["citation_validation"])
        assessment = metadata.get("completion_assessment")
        if isinstance(assessment, dict):
            completion_verdict = str(assessment.get("verdict") or completion_verdict)
            raw_failed = assessment.get("failed_criteria") or ()
            if isinstance(raw_failed, (list, tuple)):
                failed_criteria = tuple(str(item) for item in raw_failed)
    search_guard_count = sum(
        1 for event in events if event.get("event_type") == "search_guard_applied"
    )
    duplicate_query_count = sum(
        1
        for event in events
        if event.get("event_type") == "search_guard_applied"
        and event.get("payload", {}).get("reason") == "duplicate_query"
    )
    parse_warning_count = sum(
        1
        for event in events
        if str(event.get("event_type", "")).endswith("_parse_error")
        or event.get("event_type") == "controller_parse_error"
    )
    if completion_verdict != "not_assessed":
        evidence_level = "stored_model_assessment"
    return TrajectoryEvaluationReport(
        task_id=str(trajectory["task_id"]),
        status=str(trajectory["status"]),
        evidence_level=evidence_level,
        search_steps=search_steps,
        search_guard_count=search_guard_count,
        duplicate_query_count=duplicate_query_count,
        raw_candidate_count=raw_candidate_count,
        accepted_evidence_count=accepted_evidence_count,
        rejected_candidate_count=rejected_candidate_count,
        citation_validation=citation_validation,
        completion_verdict=completion_verdict,
        failed_criteria=failed_criteria,
        parse_warning_count=parse_warning_count,
    )


def _safe_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        return int(value)
    return 0
