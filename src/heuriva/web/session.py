"""Controlled session orchestration for localhost Session UI (v1.0).

Runs/resumes go through RuntimeEngine. UI never writes SQLite directly.
At most one active job per process (ADR-018).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from heuriva.core.task_contract import CriterionInput, SearchPolicy, parse_criteria
from heuriva.runtime.engine import (
    ResumeRejected,
    RuntimeEngine,
    RuntimeInterrupted,
    RuntimeProgress,
    RuntimeResult,
)
from heuriva.runtime.resume import ResumeEligibility, assess_resume_eligibility
from heuriva.storage.sqlite import SQLiteStore
from heuriva.web.queries import TrajectoryBrowser

EngineFactory = Callable[[], RuntimeEngine]

_TERMINAL_PROGRESS_STAGES = frozenset({"task_finished", "completed", "failed"})


class SessionBusy(RuntimeError):
    """Raised when a second run/resume is requested while one is active."""

    def __init__(self, *, active_task_id: str | None, job_kind: str | None) -> None:
        self.active_task_id = active_task_id
        self.job_kind = job_kind
        super().__init__(
            "session busy"
            + (f" ({job_kind} {active_task_id})" if job_kind and active_task_id else "")
        )


@dataclass
class SessionSnapshot:
    busy: bool
    job_kind: str | None = None
    task_id: str | None = None
    stage: str | None = None
    step_index: int | None = None
    message: str | None = None
    operator: str | None = None
    elapsed_seconds: float | None = None
    result_status: str | None = None
    final_answer: str | None = None
    error: str | None = None
    error_code: str | None = None
    resumed: bool = False
    updated_at: float = field(default_factory=time.time)
    log: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _ProgressLogEntry:
    ts: float
    stage: str
    message: str
    step_index: int | None = None
    operator: str | None = None
    task_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionService:
    """Single-flight run/resume gate with progress snapshots for polling."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        engine_factory: EngineFactory,
        browser: TrajectoryBrowser | None = None,
    ) -> None:
        self.store = store
        self.engine_factory = engine_factory
        self.browser = browser if browser is not None else TrajectoryBrowser(store)
        self._lock = threading.RLock()
        self._busy = False
        self._thread: threading.Thread | None = None
        self._task_ready = threading.Event()
        self._interrupt_requested = threading.Event()
        self._snapshot = SessionSnapshot(busy=False)
        self._progress_log: list[_ProgressLogEntry] = []
        self._active_engine: RuntimeEngine | None = None

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            data = asdict(self._snapshot)
            data["log"] = tuple(entry.to_dict() for entry in self._progress_log)
            return SessionSnapshot(**data)

    def _append_log(
        self,
        *,
        stage: str,
        message: str,
        step_index: int | None = None,
        operator: str | None = None,
        task_id: str | None = None,
    ) -> None:
        entry = _ProgressLogEntry(
            ts=time.time(),
            stage=stage,
            message=message,
            step_index=step_index,
            operator=operator,
            task_id=task_id,
        )
        if self._progress_log:
            prev = self._progress_log[-1]
            if (
                prev.stage == entry.stage
                and prev.message == entry.message
                and prev.step_index == entry.step_index
                and prev.operator == entry.operator
            ):
                return
        self._progress_log.append(entry)
        if len(self._progress_log) > 300:
            self._progress_log = self._progress_log[-300:]

    def resume_eligibility(self, task_id: str, *, force: bool = False) -> ResumeEligibility:
        cleaned = task_id.strip()
        if not cleaned:
            raise ValueError("task_id must not be empty")
        summary = self.store.get_task_summary(cleaned)
        step_count = self.store.count_trajectory_steps(cleaned)
        return assess_resume_eligibility(
            task_id=cleaned,
            task_status=str(summary.get("status") or "unknown"),
            step_count=step_count,
            force=force,
            has_latest_state=True,
        )

    def start_run(
        self,
        goal: str,
        *,
        criteria: tuple[CriterionInput, ...] | list[CriterionInput] | None = None,
        search_policy: SearchPolicy | str = SearchPolicy.AUTO,
        wait_for_task_id_seconds: float = 5.0,
    ) -> dict[str, Any]:
        text = goal.strip()
        if not text:
            raise ValueError("goal must not be empty")
        parsed = parse_criteria(tuple(criteria or ()))
        policy = (
            search_policy
            if isinstance(search_policy, SearchPolicy)
            else SearchPolicy(str(search_policy).strip().lower())
        )
        self._begin_job(kind="run", task_id=None)
        thread = threading.Thread(
            target=self._run_job,
            kwargs={"goal": text, "criteria": parsed, "search_policy": policy},
            name="heuriva-session-run",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()
        task_id = self._wait_for_task_id(wait_for_task_id_seconds)
        return {
            "accepted": True,
            "job_kind": "run",
            "task_id": task_id,
            "busy": True,
        }

    def start_resume(
        self,
        task_id: str,
        *,
        force: bool = False,
        wait_for_task_id_seconds: float = 5.0,
    ) -> dict[str, Any]:
        cleaned = task_id.strip()
        if not cleaned:
            raise ValueError("task_id must not be empty")
        eligibility = self.resume_eligibility(cleaned, force=force)
        if not eligibility.eligible:
            raise ResumeRejected(eligibility)
        self._begin_job(kind="resume", task_id=cleaned)
        thread = threading.Thread(
            target=self._resume_job,
            kwargs={"task_id": cleaned, "force": force},
            name="heuriva-session-resume",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()
        known = self._wait_for_task_id(wait_for_task_id_seconds) or cleaned
        return {
            "accepted": True,
            "job_kind": "resume",
            "task_id": known,
            "force": force,
            "busy": True,
        }

    def request_interrupt(self) -> dict[str, Any]:
        """Request cooperative interrupt (Session UI analogue of Ctrl+C)."""
        with self._lock:
            if not self._busy:
                raise ValueError("nothing to interrupt")
            self._interrupt_requested.set()
            task_id = self._snapshot.task_id
            self._append_log(
                stage="interrupt_requested",
                message="interrupt requested (like Ctrl+C)",
                step_index=self._snapshot.step_index,
                operator=self._snapshot.operator,
                task_id=task_id,
            )
            self._snapshot.stage = "interrupt_requested"
            self._snapshot.message = "interrupt requested"
            self._snapshot.updated_at = time.time()
            engine = self._active_engine
        if engine is not None:
            engine.cancel_io()
        return {"accepted": True, "task_id": task_id, "busy": True}

    def _begin_job(self, *, kind: str, task_id: str | None) -> None:
        with self._lock:
            if self._busy:
                raise SessionBusy(
                    active_task_id=self._snapshot.task_id,
                    job_kind=self._snapshot.job_kind,
                )
            self._busy = True
            self._interrupt_requested.clear()
            self._task_ready.clear()
            self._progress_log = []
            self._snapshot = SessionSnapshot(
                busy=True,
                job_kind=kind,
                task_id=task_id,
                stage="accepted",
                message=f"{kind} accepted",
            )
            self._append_log(
                stage="accepted",
                message=f"{kind} accepted",
                task_id=task_id,
            )
            if task_id:
                self._task_ready.set()

    def _wait_for_task_id(self, timeout: float) -> str | None:
        self._task_ready.wait(timeout=timeout)
        with self._lock:
            return self._snapshot.task_id

    def _on_progress(self, event: RuntimeProgress) -> None:
        with self._lock:
            self._snapshot.task_id = event.task_id
            self._snapshot.stage = event.stage
            self._snapshot.step_index = event.step_index
            self._snapshot.message = event.message
            self._snapshot.operator = event.operator
            self._snapshot.elapsed_seconds = event.elapsed_seconds
            self._snapshot.updated_at = time.time()
            self._append_log(
                stage=event.stage,
                message=event.message,
                step_index=event.step_index,
                operator=event.operator,
                task_id=event.task_id,
            )
            if event.task_id:
                self._task_ready.set()
            should_stop = (
                self._interrupt_requested.is_set()
                and event.stage not in _TERMINAL_PROGRESS_STAGES
            )
        if should_stop:
            raise KeyboardInterrupt

    def _run_job(
        self,
        *,
        goal: str,
        criteria: tuple[Any, ...],
        search_policy: SearchPolicy,
    ) -> None:
        try:
            engine = self.engine_factory()
            with self._lock:
                self._active_engine = engine
            result = engine.run(
                goal,
                criteria=criteria,
                search_policy=search_policy,
                progress=self._on_progress,
                interrupt_check=self._interrupt_requested.is_set,
            )
            self._finish_ok(result)
        except (RuntimeInterrupted, KeyboardInterrupt) as exc:
            self._finish_error(exc)
        except Exception as exc:
            self._finish_error(exc)
        finally:
            with self._lock:
                self._active_engine = None

    def _resume_job(self, *, task_id: str, force: bool) -> None:
        try:
            engine = self.engine_factory()
            with self._lock:
                self._active_engine = engine
            result = engine.resume(
                task_id,
                force=force,
                progress=self._on_progress,
                interrupt_check=self._interrupt_requested.is_set,
            )
            self._finish_ok(result)
        except (RuntimeInterrupted, KeyboardInterrupt) as exc:
            self._finish_error(exc)
        except Exception as exc:
            self._finish_error(exc)
        finally:
            with self._lock:
                self._active_engine = None

    def _finish_ok(self, result: RuntimeResult) -> None:
        with self._lock:
            self._busy = False
            self._interrupt_requested.clear()
            finish_msg = f"finished · {result.status}"
            self._append_log(
                stage="completed",
                message=finish_msg,
                step_index=self._snapshot.step_index,
                operator=self._snapshot.operator,
                task_id=result.task_id,
            )
            self._snapshot = SessionSnapshot(
                busy=False,
                job_kind=self._snapshot.job_kind,
                task_id=result.task_id,
                stage="completed",
                step_index=self._snapshot.step_index,
                message=finish_msg,
                operator=self._snapshot.operator,
                elapsed_seconds=self._snapshot.elapsed_seconds,
                result_status=result.status,
                final_answer=result.final_answer,
                resumed=result.resumed,
            )
            self._task_ready.set()

    def _finish_error(self, exc: BaseException) -> None:
        code: str | None = None
        message = str(exc)
        result_status: str | None = None
        stage = "failed"
        interrupted = self._interrupt_requested.is_set() or isinstance(
            exc, (RuntimeInterrupted, KeyboardInterrupt)
        )
        if isinstance(exc, ResumeRejected):
            code = "resume_rejected"
            message = str(exc)
            interrupted = False
        elif isinstance(exc, RuntimeInterrupted):
            code = "interrupted"
            message = f"interrupted task_id={exc.task_id}"
            result_status = "interrupted"
            stage = "interrupted"
        elif isinstance(exc, KeyboardInterrupt) or interrupted:
            code = "interrupted"
            with self._lock:
                tid = self._snapshot.task_id
            message = f"interrupted task_id={tid or ''}"
            result_status = "interrupted"
            stage = "interrupted"
        elif isinstance(exc, KeyError):
            code = "not_found"
            message = f"task not found: {exc.args[0] if exc.args else ''}"
        with self._lock:
            self._busy = False
            self._interrupt_requested.clear()
            self._append_log(
                stage=stage,
                message=message,
                step_index=self._snapshot.step_index,
                operator=self._snapshot.operator,
                task_id=self._snapshot.task_id,
            )
            self._snapshot = SessionSnapshot(
                busy=False,
                job_kind=self._snapshot.job_kind,
                task_id=self._snapshot.task_id,
                stage=stage,
                step_index=self._snapshot.step_index,
                message=message,
                operator=self._snapshot.operator,
                elapsed_seconds=self._snapshot.elapsed_seconds,
                result_status=result_status,
                error=message if result_status != "interrupted" else None,
                error_code=code or exc.__class__.__name__,
            )
            self._task_ready.set()
