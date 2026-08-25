from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from heuriva.core.evaluation import (
    CaseKind,
    CaseResultStatus,
    EvalCorpus,
    EvalCorpusCase,
    EvidenceLevel,
    ExpectedCaseSignals,
)
from heuriva.eval_harness import HarnessOutcome, run_harness
from heuriva.storage.sqlite import SQLiteStore

DEFAULT_CORPUS_RESOURCE = "v04_eval_corpus.yaml"
FRESH_LIVE_ENV = "HEURIVA_EVAL_SUITE_FRESH_LIVE"


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
    termination_reason: str | None = None
    search_guard_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    kind: str
    evidence_level: str
    status: str
    expected_quality_signal: str
    mismatches: tuple[str, ...] = ()
    task_id: str | None = None
    trajectory_report: TrajectoryEvaluationReport | None = None
    search_provider_calls: int | None = None
    notes: str = ""
    is_product_proof: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.trajectory_report is not None:
            payload["trajectory_report"] = self.trajectory_report.to_dict()
        return payload


@dataclass(frozen=True)
class PromotionStats:
    known_good_total: int
    known_good_pass: int
    known_bad_total: int
    known_bad_pass: int
    false_positives: int
    false_negatives: int
    recommend_enforce: bool
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvalSuiteReport:
    corpus_version: str
    corpus_path: str
    include_fresh_live: bool
    case_results: tuple[EvalCaseResult, ...]
    totals_by_status: dict[str, int] = field(default_factory=dict)
    totals_by_evidence_level: dict[str, int] = field(default_factory=dict)
    aggregate_signals: dict[str, int] = field(default_factory=dict)
    promotion: PromotionStats | None = None
    disclaimer: str = (
        "Deterministic and fake-integration results are regression signals, "
        "not product proof of model correctness."
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus_version": self.corpus_version,
            "corpus_path": self.corpus_path,
            "include_fresh_live": self.include_fresh_live,
            "case_results": [result.to_dict() for result in self.case_results],
            "totals_by_status": self.totals_by_status,
            "totals_by_evidence_level": self.totals_by_evidence_level,
            "aggregate_signals": self.aggregate_signals,
            "promotion": self.promotion.to_dict() if self.promotion else None,
            "disclaimer": self.disclaimer,
        }


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
    search_guard_events = [
        event for event in events if event.get("event_type") == "search_guard_applied"
    ]
    search_guard_count = len(search_guard_events)
    search_guard_reasons = tuple(
        str(event.get("payload", {}).get("reason") or "")
        for event in search_guard_events
        if event.get("payload", {}).get("reason")
    )
    duplicate_query_count = sum(1 for reason in search_guard_reasons if reason == "duplicate_query")
    parse_warning_count = sum(
        1
        for event in events
        if str(event.get("event_type", "")).endswith("_parse_error")
        or event.get("event_type") == "controller_parse_error"
    )
    if completion_verdict != "not_assessed" and evidence_level == "deterministic":
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
        termination_reason=(
            None
            if trajectory.get("termination_reason") is None
            else str(trajectory.get("termination_reason"))
        ),
        search_guard_reasons=search_guard_reasons,
    )


def default_corpus_path() -> Path:
    resource = resources.files("heuriva.data").joinpath(DEFAULT_CORPUS_RESOURCE)
    with resources.as_file(resource) as path:
        return Path(path)


def load_eval_corpus(path: Path | str | None = None) -> tuple[EvalCorpus, Path]:
    corpus_path = Path(path) if path is not None else default_corpus_path()
    loaded = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("eval corpus must be a YAML mapping")
    corpus = EvalCorpus.model_validate(loaded)
    return corpus, corpus_path


def run_eval_suite(
    *,
    corpus_path: Path | str | None = None,
    sqlite_path: Path | str | None = None,
    include_fresh_live: bool | None = None,
    workdir: Path | None = None,
) -> EvalSuiteReport:
    corpus, resolved_corpus_path = load_eval_corpus(corpus_path)
    fresh_live_enabled = (
        include_fresh_live if include_fresh_live is not None else _env_flag(FRESH_LIVE_ENV)
    )
    store = SQLiteStore(Path(sqlite_path).expanduser()) if sqlite_path is not None else None
    results: list[EvalCaseResult] = []
    owned_workdir = workdir is None
    root = workdir or Path(tempfile.mkdtemp(prefix="heuriva-eval-suite-"))
    try:
        for case in corpus.cases:
            case_dir = root / case.id
            case_dir.mkdir(parents=True, exist_ok=True)
            results.append(
                _run_case(
                    case,
                    workdir=case_dir,
                    store=store,
                    include_fresh_live=fresh_live_enabled,
                )
            )
    finally:
        if owned_workdir:
            # Temporary harness DBs are disposable; leave cleanup to OS/tmp.
            pass

    promotion = compute_promotion_stats(tuple(results))
    return EvalSuiteReport(
        corpus_version=corpus.version,
        corpus_path=str(resolved_corpus_path),
        include_fresh_live=fresh_live_enabled,
        case_results=tuple(results),
        totals_by_status=_count_by(results, key=lambda item: item.status),
        totals_by_evidence_level=_count_by(results, key=lambda item: item.evidence_level),
        aggregate_signals=_aggregate_signals(results),
        promotion=promotion,
    )


def compute_promotion_stats(
    results: tuple[EvalCaseResult, ...] | list[EvalCaseResult],
) -> PromotionStats:
    scored = [
        result
        for result in results
        if result.status in {CaseResultStatus.PASS.value, CaseResultStatus.FAIL.value}
        and result.evidence_level
        in {EvidenceLevel.SYNTHETIC.value, EvidenceLevel.FAKE_INTEGRATION.value}
    ]
    known_good = [result for result in scored if result.kind == CaseKind.KNOWN_GOOD.value]
    known_bad = [result for result in scored if result.kind == CaseKind.KNOWN_BAD.value]
    known_good_pass = sum(
        1 for result in known_good if result.status == CaseResultStatus.PASS.value
    )
    known_bad_pass = sum(1 for result in known_bad if result.status == CaseResultStatus.PASS.value)
    false_negatives = sum(
        1 for result in known_good if result.status == CaseResultStatus.FAIL.value
    )
    false_positives = sum(1 for result in known_bad if result.status == CaseResultStatus.FAIL.value)
    # known_bad cases encode undesirable outcomes that the runtime should catch.
    # Suite "pass" means the harness correctly detected the bad outcome.
    # FP = known_bad suite failure (bad outcome not caught).
    # FN = known_good suite failure (good behavior not reproduced).
    recommend = (
        len(known_good) >= 2
        and len(known_bad) >= 1
        and false_positives == 0
        and false_negatives == 0
        and known_good_pass == len(known_good)
        and known_bad_pass == len(known_bad)
    )
    if recommend:
        rationale = (
            "Deterministic/fake corpus currently shows zero false positives and "
            "zero false negatives. This is necessary but not sufficient for "
            "semantic enforce; keep quality modes at observe until live corpus "
            "evidence is also reviewed."
        )
    else:
        rationale = (
            "Promotion gate not met for semantic enforce. Keep "
            "evidence_relevance_mode and completion_check_mode at observe "
            "unless a narrower deterministic guard is already covered by tests."
        )
    return PromotionStats(
        known_good_total=len(known_good),
        known_good_pass=known_good_pass,
        known_bad_total=len(known_bad),
        known_bad_pass=known_bad_pass,
        false_positives=false_positives,
        false_negatives=false_negatives,
        recommend_enforce=False,
        rationale=rationale,
    )


def render_suite_report(report: EvalSuiteReport) -> str:
    lines = [
        f"corpus_version: {report.corpus_version}",
        f"corpus_path: {report.corpus_path}",
        f"include_fresh_live: {report.include_fresh_live}",
        f"disclaimer: {report.disclaimer}",
        "totals_by_status:",
    ]
    for key, value in sorted(report.totals_by_status.items()):
        lines.append(f"  {key}: {value}")
    lines.append("totals_by_evidence_level:")
    for key, value in sorted(report.totals_by_evidence_level.items()):
        lines.append(f"  {key}: {value}")
    lines.append("aggregate_signals:")
    for key, value in sorted(report.aggregate_signals.items()):
        lines.append(f"  {key}: {value}")
    if report.promotion is not None:
        promo = report.promotion
        lines.extend(
            [
                "promotion:",
                f"  known_good_pass: {promo.known_good_pass}/{promo.known_good_total}",
                f"  known_bad_pass: {promo.known_bad_pass}/{promo.known_bad_total}",
                f"  false_positives: {promo.false_positives}",
                f"  false_negatives: {promo.false_negatives}",
                f"  recommend_enforce: {promo.recommend_enforce}",
                f"  rationale: {promo.rationale}",
            ]
        )
    lines.append("cases:")
    for result in report.case_results:
        lines.append(
            f"  - {result.case_id}: {result.status} [{result.evidence_level}/{result.kind}]"
        )
        lines.append(f"    signal: {result.expected_quality_signal}")
        if result.mismatches:
            lines.append(f"    mismatches: {', '.join(result.mismatches)}")
        if result.notes:
            lines.append(f"    notes: {result.notes}")
        if result.trajectory_report is not None:
            traj = result.trajectory_report
            lines.append(
                "    metrics: "
                f"status={traj.status}, "
                f"guards={traj.search_guard_count}, "
                f"raw={traj.raw_candidate_count}, "
                f"accepted={traj.accepted_evidence_count}, "
                f"rejected={traj.rejected_candidate_count}, "
                f"citation={traj.citation_validation}, "
                f"completion={traj.completion_verdict}, "
                f"parse_warnings={traj.parse_warning_count}"
            )
    return "\n".join(lines)


def _run_case(
    case: EvalCorpusCase,
    *,
    workdir: Path,
    store: SQLiteStore | None,
    include_fresh_live: bool,
) -> EvalCaseResult:
    if case.evidence_level is EvidenceLevel.FRESH_LIVE and not include_fresh_live:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.SKIPPED.value,
            expected_quality_signal=case.expected_quality_signal,
            notes="fresh_live requires --include-fresh-live or "
            f"{FRESH_LIVE_ENV}=1; not product proof by default",
            is_product_proof=False,
        )

    if case.evidence_level is EvidenceLevel.STORED_LIVE:
        return _run_stored_live_case(case, store=store)

    if case.evidence_level is EvidenceLevel.FRESH_LIVE:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.SKIPPED.value,
            expected_quality_signal=case.expected_quality_signal,
            notes=(
                "fresh_live opted in, but v0.4 does not auto-call live models; "
                "record live evidence via ignored checklist and stored task IDs"
            ),
            is_product_proof=False,
        )

    if not case.harness:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.INSUFFICIENT_EVIDENCE.value,
            expected_quality_signal=case.expected_quality_signal,
            notes="runnable case is missing a harness name",
            is_product_proof=False,
        )

    outcome = run_harness(case.harness, workdir)
    return _score_harness_case(case, outcome)


def _run_stored_live_case(case: EvalCorpusCase, *, store: SQLiteStore | None) -> EvalCaseResult:
    if not case.task_id:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.MISSING.value,
            expected_quality_signal=case.expected_quality_signal,
            notes="stored_live case has no task_id; skipped without failing the suite",
            is_product_proof=False,
        )
    if store is None:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.MISSING.value,
            task_id=case.task_id,
            expected_quality_signal=case.expected_quality_signal,
            notes="no sqlite path provided for stored_live summary",
            is_product_proof=False,
        )
    try:
        data = store.get_trajectory(case.task_id)
    except KeyError:
        return EvalCaseResult(
            case_id=case.id,
            kind=case.kind.value,
            evidence_level=case.evidence_level.value,
            status=CaseResultStatus.MISSING.value,
            task_id=case.task_id,
            expected_quality_signal=case.expected_quality_signal,
            notes="task_id not found in local SQLite store",
            is_product_proof=False,
        )
    report = evaluate_trajectory(data, evidence_level=EvidenceLevel.STORED_LIVE.value)
    mismatches = _compare_expected(case.expected, report, search_provider_calls=None)
    status = CaseResultStatus.PASS if not mismatches else CaseResultStatus.FAIL
    return EvalCaseResult(
        case_id=case.id,
        kind=case.kind.value,
        evidence_level=case.evidence_level.value,
        status=status.value,
        expected_quality_signal=case.expected_quality_signal,
        mismatches=mismatches,
        task_id=case.task_id,
        trajectory_report=report,
        notes="stored_live summary is observational and not fresh product proof",
        is_product_proof=False,
    )


def _score_harness_case(case: EvalCorpusCase, outcome: HarnessOutcome) -> EvalCaseResult:
    report = evaluate_trajectory(
        outcome.trajectory,
        evidence_level=case.evidence_level.value,
    )
    mismatches = _compare_expected(
        case.expected,
        report,
        search_provider_calls=outcome.search_provider_calls,
    )
    status = CaseResultStatus.PASS if not mismatches else CaseResultStatus.FAIL
    return EvalCaseResult(
        case_id=case.id,
        kind=case.kind.value,
        evidence_level=case.evidence_level.value,
        status=status.value,
        expected_quality_signal=case.expected_quality_signal,
        mismatches=mismatches,
        task_id=report.task_id,
        trajectory_report=report,
        search_provider_calls=outcome.search_provider_calls,
        notes=outcome.notes or "synthetic/fake harness result; not product proof",
        is_product_proof=False,
    )


def _compare_expected(
    expected: ExpectedCaseSignals,
    report: TrajectoryEvaluationReport,
    *,
    search_provider_calls: int | None,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    if expected.status is not None and report.status != expected.status:
        mismatches.append(f"status expected={expected.status} actual={report.status}")
    if (
        expected.termination_reason is not None
        and report.termination_reason != expected.termination_reason
    ):
        mismatches.append(
            "termination_reason expected="
            f"{expected.termination_reason} actual={report.termination_reason}"
        )
    if (
        expected.search_provider_calls is not None
        and search_provider_calls is not None
        and search_provider_calls != expected.search_provider_calls
    ):
        mismatches.append(
            "search_provider_calls expected="
            f"{expected.search_provider_calls} actual={search_provider_calls}"
        )
    if expected.search_guard_reasons:
        actual_reasons = set(report.search_guard_reasons)
        missing = [
            reason for reason in expected.search_guard_reasons if reason not in actual_reasons
        ]
        if missing:
            mismatches.append(f"missing search_guard_reasons={','.join(missing)}")
    if (
        expected.min_search_guards is not None
        and report.search_guard_count < expected.min_search_guards
    ):
        mismatches.append(
            "min_search_guards expected>="
            f"{expected.min_search_guards} actual={report.search_guard_count}"
        )
    if (
        expected.min_raw_candidates is not None
        and report.raw_candidate_count < expected.min_raw_candidates
    ):
        mismatches.append(
            "min_raw_candidates expected>="
            f"{expected.min_raw_candidates} actual={report.raw_candidate_count}"
        )
    if (
        expected.min_accepted_evidence is not None
        and report.accepted_evidence_count < expected.min_accepted_evidence
    ):
        mismatches.append(
            "min_accepted_evidence expected>="
            f"{expected.min_accepted_evidence} actual={report.accepted_evidence_count}"
        )
    if (
        expected.min_rejected_candidates is not None
        and report.rejected_candidate_count < expected.min_rejected_candidates
    ):
        mismatches.append(
            "min_rejected_candidates expected>="
            f"{expected.min_rejected_candidates} actual={report.rejected_candidate_count}"
        )
    if (
        expected.citation_validation is not None
        and report.citation_validation != expected.citation_validation
    ):
        mismatches.append(
            "citation_validation expected="
            f"{expected.citation_validation} actual={report.citation_validation}"
        )
    if (
        expected.completion_verdict is not None
        and report.completion_verdict != expected.completion_verdict
    ):
        mismatches.append(
            "completion_verdict expected="
            f"{expected.completion_verdict} actual={report.completion_verdict}"
        )
    return tuple(mismatches)


def _aggregate_signals(results: list[EvalCaseResult]) -> dict[str, int]:
    totals = {
        "search_steps": 0,
        "search_guards": 0,
        "raw_candidates": 0,
        "accepted_evidence": 0,
        "rejected_candidates": 0,
        "parse_warnings": 0,
        "completion_pass": 0,
        "completion_fail": 0,
        "citation_passed": 0,
        "citation_failed": 0,
    }
    for result in results:
        report = result.trajectory_report
        if report is None:
            continue
        totals["search_steps"] += report.search_steps
        totals["search_guards"] += report.search_guard_count
        totals["raw_candidates"] += report.raw_candidate_count
        totals["accepted_evidence"] += report.accepted_evidence_count
        totals["rejected_candidates"] += report.rejected_candidate_count
        totals["parse_warnings"] += report.parse_warning_count
        if report.completion_verdict == "pass":
            totals["completion_pass"] += 1
        elif report.completion_verdict == "fail":
            totals["completion_fail"] += 1
        if report.citation_validation == "passed":
            totals["citation_passed"] += 1
        elif report.citation_validation == "failed":
            totals["citation_failed"] += 1
    return totals


def _count_by(results: list[EvalCaseResult], *, key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        value = str(key(result))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _env_flag(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        return int(value)
    return 0
