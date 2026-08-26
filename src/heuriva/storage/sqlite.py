from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from heuriva.core.common import new_id, utc_now
from heuriva.core.decision import Decision
from heuriva.core.event import RuntimeEvent
from heuriva.core.observation import Observation
from heuriva.core.state import CognitiveState, StateStatus
from heuriva.runtime.state_delta import calculate_state_delta

SCHEMA_VERSION = 2


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def schema_status(path: str | Path) -> str:
        db_path = Path(path).expanduser()
        if not db_path.exists():
            return "not initialized"
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        except sqlite3.Error:
            return "unsupported"
        if row is None:
            return "unsupported"
        version = int(row[0])
        if version == SCHEMA_VERSION:
            return "current"
        if version < SCHEMA_VERSION:
            return "outdated"
        return "unsupported"

    def create_task_with_trajectory(
        self, state: CognitiveState, *, config_snapshot: dict[str, Any]
    ) -> str:
        trajectory_id = new_id()
        now = utc_now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                  id, goal, status, config_snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.task_id,
                    state.goal,
                    state.status.value,
                    json.dumps(config_snapshot, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._insert_state(conn, state)
            conn.execute(
                """
                INSERT INTO trajectories (
                  id, task_id, initial_state_id, status, termination_reason,
                  started_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory_id,
                    state.task_id,
                    state.id,
                    "running",
                    "running",
                    now,
                    json.dumps({"schema_version": SCHEMA_VERSION}, ensure_ascii=False),
                ),
            )
            conn.commit()
        return trajectory_id

    def commit_step(
        self,
        *,
        state_before: CognitiveState,
        decision: Decision,
        observation: Observation,
        state_after: CognitiveState,
    ) -> str:
        step_id = new_id()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                self._insert_decision(conn, decision)
                self._insert_observation(conn, observation)
                self._insert_state(conn, state_after)
                conn.execute(
                    """
                    INSERT INTO trajectory_steps (
                      id, task_id, step_index, state_before_id, decision_id,
                      observation_id, state_after_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step_id,
                        decision.task_id,
                        decision.step_index,
                        state_before.id,
                        decision.id,
                        observation.id,
                        state_after.id,
                        utc_now().isoformat(),
                    ),
                )
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                    (state_after.status.value, utc_now().isoformat(), state_after.task_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return step_id

    def finalize_task(
        self,
        *,
        task_id: str,
        final_state: CognitiveState,
        status: str,
        termination_reason: str,
        final_answer: str | None,
    ) -> None:
        now = utc_now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                self._insert_state_if_missing(conn, final_state)
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, error_code = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        None if status == "done" else termination_reason,
                        now,
                        now,
                        task_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE trajectories
                    SET final_state_id = ?, status = ?, final_answer = ?,
                        termination_reason = ?, completed_at = ?
                    WHERE task_id = ?
                    """,
                    (final_state.id, status, final_answer, termination_reason, now, task_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def log_event(self, event: RuntimeEvent) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO runtime_events (
                  id, task_id, step_index, event_type, level, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.task_id,
                    event.step_index,
                    event.event_type,
                    event.level.value,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.created_at.isoformat(),
                ),
            )
            conn.commit()

    def list_tasks(self, *, limit: int = 100, offset: int = 0) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"{_TASK_SUMMARY_SQL} ORDER BY t.updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_task_summary(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"{_TASK_SUMMARY_SQL} WHERE t.id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def count_trajectory_steps(self, task_id: str) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM trajectory_steps WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def trajectory_step_fingerprints(self, task_id: str) -> tuple[str, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, step_index, decision_id, observation_id,
                       state_before_id, state_after_id
                FROM trajectory_steps
                WHERE task_id = ?
                ORDER BY step_index
                """,
                (task_id,),
            ).fetchall()
        return tuple(
            "|".join(
                (
                    str(row["step_index"]),
                    row["id"],
                    row["decision_id"],
                    row["observation_id"],
                    row["state_before_id"],
                    row["state_after_id"],
                )
            )
            for row in rows
        )

    def load_resume_bundle(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(task_id)
            trajectory = conn.execute(
                "SELECT * FROM trajectories WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if trajectory is None:
                raise KeyError(task_id)
            latest_state_row = conn.execute(
                """
                SELECT state_json
                FROM states
                WHERE task_id = ?
                ORDER BY revision_index DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if latest_state_row is None:
                raise RuntimeError(f"task {task_id} has no CognitiveState snapshots")
            step_rows = conn.execute(
                """
                SELECT
                  ts.step_index,
                  ts.created_at,
                  d.decision_json,
                  o.observation_json,
                  state_before.state_json AS state_before_json,
                  state_after.state_json AS state_after_json
                FROM trajectory_steps ts
                JOIN decisions d ON d.id = ts.decision_id
                JOIN observations o ON o.id = ts.observation_id
                JOIN states state_before ON state_before.id = ts.state_before_id
                JOIN states state_after ON state_after.id = ts.state_after_id
                WHERE ts.task_id = ?
                ORDER BY ts.step_index
                """,
                (task_id,),
            ).fetchall()
        config_snapshot = json.loads(task["config_snapshot_json"])
        return {
            "task_id": task_id,
            "goal": task["goal"],
            "task_status": task["status"],
            "config_snapshot": config_snapshot,
            "trajectory_status": trajectory["status"],
            "latest_state": CognitiveState.model_validate(
                json.loads(latest_state_row["state_json"])
            ),
            "steps": [_step_row_to_dict(row) for row in step_rows],
            "step_fingerprints": self.trajectory_step_fingerprints(task_id),
        }

    def prepare_resume(
        self,
        *,
        task_id: str,
        resume_state: CognitiveState,
        reason: str,
    ) -> None:
        now = utc_now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                self._insert_state_if_missing(conn, resume_state)
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, error_code = NULL, updated_at = ?, completed_at = NULL
                    WHERE id = ?
                    """,
                    (StateStatus.RUNNING.value, now, task_id),
                )
                conn.execute(
                    """
                    UPDATE trajectories
                    SET final_state_id = NULL, status = ?, final_answer = NULL,
                        termination_reason = ?, completed_at = NULL
                    WHERE task_id = ?
                    """,
                    ("running", reason, task_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_trajectory(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            trajectory = conn.execute(
                "SELECT * FROM trajectories WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if trajectory is None:
                raise KeyError(task_id)
            steps = conn.execute(
                """
                SELECT
                  ts.step_index,
                  ts.created_at,
                  d.decision_json,
                  o.observation_json,
                  state_before.state_json AS state_before_json,
                  state_after.state_json AS state_after_json
                FROM trajectory_steps ts
                JOIN decisions d ON d.id = ts.decision_id
                JOIN observations o ON o.id = ts.observation_id
                JOIN states state_before ON state_before.id = ts.state_before_id
                JOIN states state_after ON state_after.id = ts.state_after_id
                WHERE ts.task_id = ?
                ORDER BY ts.step_index
                """,
                (task_id,),
            ).fetchall()
            events = conn.execute(
                """
                SELECT event_type, level, payload_json, created_at
                FROM runtime_events
                WHERE task_id = ?
                ORDER BY created_at
                """,
                (task_id,),
            ).fetchall()
        return {
            "trajectory": _row_to_dict(trajectory),
            "steps": [_step_row_to_dict(row) for row in steps],
            "events": [
                {
                    "event_type": row["event_type"],
                    "level": row["level"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in events
            ],
        }

    def save_eval_run(
        self,
        *,
        task_id: str,
        trajectory_id: str | None,
        case_id: str | None,
        judge_mode: str,
        deterministic_verdict: str,
        judge_verdict: str,
        disagreement_bucket: str,
        model: str,
        prompt_version: str,
        prompt_hash: str,
        provenance: dict[str, Any],
        result: dict[str, Any],
        failure_code: str | None = None,
    ) -> str:
        eval_run_id = new_id()
        now = utc_now().isoformat()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO eval_runs (
                  id, task_id, trajectory_id, case_id, judge_mode,
                  deterministic_verdict, judge_verdict, disagreement_bucket,
                  model, prompt_version, prompt_hash, failure_code,
                  provenance_json, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_run_id,
                    task_id,
                    trajectory_id,
                    case_id,
                    judge_mode,
                    deterministic_verdict,
                    judge_verdict,
                    disagreement_bucket,
                    model,
                    prompt_version,
                    prompt_hash,
                    failure_code,
                    json.dumps(provenance, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        return eval_run_id

    def list_eval_runs(self, task_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                  id, task_id, trajectory_id, case_id, judge_mode,
                  deterministic_verdict, judge_verdict, disagreement_bucket,
                  model, prompt_version, prompt_hash, failure_code,
                  provenance_json, result_json, created_at
                FROM eval_runs
                WHERE task_id = ?
                ORDER BY created_at
                """,
                (task_id,),
            ).fetchall()
        return tuple(_eval_run_row_to_dict(row) for row in rows)

    def get_eval_run(self, eval_run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                  id, task_id, trajectory_id, case_id, judge_mode,
                  deterministic_verdict, judge_verdict, disagreement_bucket,
                  model, prompt_version, prompt_hash, failure_code,
                  provenance_json, result_json, created_at
                FROM eval_runs
                WHERE id = ?
                """,
                (eval_run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(eval_run_id)
        return _eval_run_row_to_dict(row)

    def leak_task_ids_from_eval_runs(self) -> tuple[str, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT task_id
                FROM eval_runs
                WHERE disagreement_bucket = 'deterministic_pass_judge_fail'
                  AND json_extract(result_json, '$.deterministic.citation_validation')
                      = 'passed'
                  AND deterministic_verdict = 'pass'
                ORDER BY task_id
                """
            ).fetchall()
        return tuple(str(row["task_id"]) for row in rows)

    @staticmethod
    def stale_running_summary(
        path: str | Path, *, max_age_seconds: int
    ) -> dict[str, int | str | None]:
        db_path = Path(path).expanduser()
        if SQLiteStore.schema_status(db_path) != "current":
            return {"count": 0, "oldest_task_id": None}
        cutoff = utc_now().timestamp() - max_age_seconds
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, updated_at FROM tasks WHERE status = 'running'"
            ).fetchall()
        stale_rows = tuple(row for row in rows if _iso_timestamp(row["updated_at"]) < cutoff)
        oldest = min(stale_rows, key=lambda row: _iso_timestamp(row["updated_at"]), default=None)
        return {
            "count": len(stale_rows),
            "oldest_task_id": oldest["id"] if oldest is not None else None,
        }

    def _initialize(self) -> None:
        with closing(self._connect(initialize=False)) as conn:
            conn.executescript(SCHEMA_SQL)
            row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            else:
                version = int(row[0])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported SQLite schema version: {version} > {SCHEMA_VERSION}"
                    )
                if version < SCHEMA_VERSION:
                    _migrate_schema(conn, from_version=version)
            conn.commit()

    def _connect(self, *, initialize: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if initialize:
            status = self.schema_status(self.path)
            if status != "current":
                raise RuntimeError(f"unsupported SQLite schema status: {status}")
        return conn

    @staticmethod
    def _insert_state(conn: sqlite3.Connection, state: CognitiveState) -> None:
        conn.execute(
            """
            INSERT INTO states (
              id, task_id, revision_index, step_index, status, state_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.id,
                state.task_id,
                state.revision_index,
                state.step_index,
                state.status.value,
                state.model_dump_json(),
                state.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_state_if_missing(conn: sqlite3.Connection, state: CognitiveState) -> None:
        existing = conn.execute("SELECT id FROM states WHERE id = ?", (state.id,)).fetchone()
        if existing is None:
            SQLiteStore._insert_state(conn, state)

    @staticmethod
    def _insert_decision(conn: sqlite3.Connection, decision: Decision) -> None:
        conn.execute(
            """
            INSERT INTO decisions (
              id, task_id, state_id, step_index, operator, objective,
              reason, decision_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.id,
                decision.task_id,
                decision.state_id,
                decision.step_index,
                decision.operator.value,
                decision.objective,
                decision.reason,
                decision.model_dump_json(),
                decision.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_observation(conn: sqlite3.Connection, observation: Observation) -> None:
        conn.execute(
            """
            INSERT INTO observations (
              id, task_id, decision_id, kind, status, executor_kind, content,
              error_json, observation_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.id,
                observation.task_id,
                observation.decision_id,
                observation.kind.value,
                observation.status.value,
                observation.executor_kind,
                observation.content,
                observation.error.model_dump_json() if observation.error else None,
                observation.model_dump_json(),
                observation.created_at.isoformat(),
            ),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("metadata_json",):
        if key in result and result[key] is not None:
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
    return result


def _step_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    state_before_json = json.loads(row["state_before_json"])
    state_after_json = json.loads(row["state_after_json"])
    state_before = CognitiveState.model_validate(state_before_json)
    state_after = CognitiveState.model_validate(state_after_json)
    return {
        "step_index": row["step_index"],
        "created_at": row["created_at"],
        "decision": json.loads(row["decision_json"]),
        "observation": json.loads(row["observation_json"]),
        "state_before": state_before_json,
        "state_after": state_after_json,
        "state_delta": calculate_state_delta(state_before, state_after).to_dict(),
    }


def _eval_run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "trajectory_id": row["trajectory_id"],
        "case_id": row["case_id"],
        "judge_mode": row["judge_mode"],
        "deterministic_verdict": row["deterministic_verdict"],
        "judge_verdict": row["judge_verdict"],
        "disagreement_bucket": row["disagreement_bucket"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "prompt_hash": row["prompt_hash"],
        "failure_code": row["failure_code"],
        "provenance": json.loads(row["provenance_json"]),
        "result": json.loads(row["result_json"]),
        "created_at": row["created_at"],
    }


def _migrate_schema(conn: sqlite3.Connection, *, from_version: int) -> None:
    # SCHEMA_SQL already applies CREATE TABLE IF NOT EXISTS for new tables.
    # Explicit version bumps keep old task rows readable without rewrite.
    if from_version < 2:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_runs (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              trajectory_id TEXT,
              case_id TEXT,
              judge_mode TEXT NOT NULL,
              deterministic_verdict TEXT NOT NULL,
              judge_verdict TEXT NOT NULL,
              disagreement_bucket TEXT NOT NULL,
              model TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              prompt_hash TEXT NOT NULL,
              failure_code TEXT,
              provenance_json TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_runs_task_created "
            "ON eval_runs(task_id, created_at)"
        )
    conn.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))


_TASK_SUMMARY_SQL = """
SELECT
  t.id AS task_id,
  t.goal,
  t.status,
  t.created_at,
  t.updated_at,
  t.completed_at,
  tr.final_answer,
  tr.termination_reason,
  (
    SELECT COUNT(*)
    FROM trajectory_steps ts
    WHERE ts.task_id = t.id
  ) AS step_count
FROM tasks t
LEFT JOIN trajectories tr ON tr.task_id = t.id
"""


def _iso_timestamp(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  config_snapshot_json TEXT NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS states (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  revision_index INTEGER NOT NULL,
  step_index INTEGER NOT NULL,
  status TEXT NOT NULL,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(task_id, revision_index),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  state_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  operator TEXT NOT NULL,
  objective TEXT NOT NULL,
  reason TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(task_id, step_index),
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(state_id) REFERENCES states(id)
);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  executor_kind TEXT NOT NULL,
  content TEXT NOT NULL,
  error_json TEXT,
  observation_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(decision_id),
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS trajectory_steps (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  state_before_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  observation_id TEXT NOT NULL,
  state_after_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(task_id, step_index),
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(state_before_id) REFERENCES states(id),
  FOREIGN KEY(decision_id) REFERENCES decisions(id),
  FOREIGN KEY(observation_id) REFERENCES observations(id),
  FOREIGN KEY(state_after_id) REFERENCES states(id)
);

CREATE TABLE IF NOT EXISTS trajectories (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  initial_state_id TEXT NOT NULL,
  final_state_id TEXT,
  status TEXT NOT NULL,
  final_answer TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  metadata_json TEXT NOT NULL,
  termination_reason TEXT NOT NULL DEFAULT 'running',
  UNIQUE(task_id),
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(initial_state_id) REFERENCES states(id),
  FOREIGN KEY(final_state_id) REFERENCES states(id)
);

CREATE TABLE IF NOT EXISTS runtime_events (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  step_index INTEGER,
  event_type TEXT NOT NULL,
  level TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  trajectory_id TEXT,
  case_id TEXT,
  judge_mode TEXT NOT NULL,
  deterministic_verdict TEXT NOT NULL,
  judge_verdict TEXT NOT NULL,
  disagreement_bucket TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  failure_code TEXT,
  provenance_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_states_task_revision ON states(task_id, revision_index);
CREATE INDEX IF NOT EXISTS idx_states_task_step ON states(task_id, step_index);
CREATE INDEX IF NOT EXISTS idx_trajectory_steps_task_step ON trajectory_steps(task_id, step_index);
CREATE INDEX IF NOT EXISTS idx_runtime_events_task_created ON runtime_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_eval_runs_task_created ON eval_runs(task_id, created_at);
"""
