from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from heuriva.core.decision import AnswerParams, DecisionDraft, bind_decision
from heuriva.core.observation import Observation, ObservationKind, ObservationStatus
from heuriva.core.operator import Operator
from heuriva.core.state import CognitiveState
from heuriva.storage.sqlite import SQLiteStore


def test_sqlite_store_persists_and_reads_trajectory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    trajectory_id = store.create_task_with_trajectory(state, config_snapshot={"model": "fake"})
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANSWER,
            objective="answer",
            reason="done",
            success_criteria=("final",),
            params=AnswerParams(),
            confidence=0.8,
        ),
        state,
    )
    observation = Observation(
        task_id=state.task_id,
        decision_id=decision.id,
        kind=ObservationKind.ANSWER,
        status=ObservationStatus.SUCCESS,
        content="Final",
        executor_kind="llm",
    )
    next_state = state.advance(status="done")

    store.commit_step(
        state_before=state,
        decision=decision,
        observation=observation,
        state_after=next_state,
    )
    store.finalize_task(
        task_id=state.task_id,
        final_state=next_state,
        status="done",
        termination_reason="answer",
        final_answer="Final",
    )

    loaded = store.get_trajectory(state.task_id)

    assert trajectory_id == loaded["trajectory"]["id"]
    assert loaded["trajectory"]["final_answer"] == "Final"
    assert len(loaded["steps"]) == 1
    assert loaded["steps"][0]["decision"]["operator"] == "ANSWER"
    assert "state_delta" in loaded["steps"][0]


def test_sqlite_commit_step_rolls_back_on_failure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    state = CognitiveState.new(task_id="task-1", goal="Explain")
    store.create_task_with_trajectory(state, config_snapshot={})
    decision = bind_decision(
        DecisionDraft(
            operator=Operator.ANSWER,
            objective="answer",
            reason="done",
            success_criteria=("final",),
            params=AnswerParams(),
            confidence=0.8,
        ),
        state,
    )
    observation = Observation(
        task_id=state.task_id,
        decision_id=decision.id,
        kind=ObservationKind.ANSWER,
        status=ObservationStatus.SUCCESS,
        content="Final",
        executor_kind="llm",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.commit_step(
            state_before=state,
            decision=decision,
            observation=observation,
            state_after=state,  # duplicate state id forces rollback
        )

    loaded = store.get_trajectory(state.task_id)
    assert loaded["steps"] == []
