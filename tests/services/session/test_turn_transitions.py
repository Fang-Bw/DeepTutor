"""Shared state-transition contract for SQLite and the PocketBase SDK adapter.

PocketBase uses a local collection fake here; this checks validation and row
updates, not server-side transaction/compare-and-swap guarantees.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deeptutor.core.turn_state import TurnStatus, can_transition_turn
from deeptutor.runtime.coordination.types import TurnStatus as CoordinationTurnStatus
from deeptutor.services.session.pocketbase_store import PocketBaseSessionStore
from deeptutor.services.session.sqlite_store import SQLiteSessionStore

STATES = ("queued", "running", "waiting_input", "completed", "failed", "cancelled")
# Rows and columns follow STATES. This is the public contract, including
# same-state metadata writes and compatibility with persisted queued records.
ALLOWED_EDGES = (
    (True, True, False, True, True, True),
    (False, True, True, True, True, True),
    (False, True, True, True, True, True),
    (False, False, False, True, False, False),
    (False, False, False, False, True, False),
    (False, False, False, False, False, True),
)


class _TurnCollection:
    def __init__(self, record):
        self.record = record

    def get_full_list(self, *, query_params):
        if query_params["filter"] == f'turn_id="{self.record.turn_id}"':
            return [self.record]
        return []

    def update(self, record_id, data):
        assert record_id == self.record.id
        for name, value in data.items():
            setattr(self.record, name, value)
        return self.record


@pytest.fixture(params=("sqlite", "pocketbase"))
def make_turn(request, tmp_path, monkeypatch):
    async def make(status="running"):
        if request.param == "sqlite":
            store = SQLiteSessionStore(tmp_path / "turns.db")
            session = await store.ensure_session("test-session")
            turn = await store.begin_turn(session["id"], turn_id="test-turn", fencing_token=7)
            assert turn["status"] == "running"
            with store._connect() as conn:
                # Seed persisted states without permitting an illegal public
                # transition such as running -> queued in setup.
                conn.execute(
                    "UPDATE turns SET status = ?, finished_at = ? WHERE id = ?",
                    (status, 1.0 if status in STATES[3:] else None, turn["id"]),
                )
        else:
            record = SimpleNamespace(
                id="pb-turn",
                turn_id="test-turn",
                session_id="test-session",
                status=status,
                state_version=1,
                fencing_token=7,
                finished_at=1.0 if status in STATES[3:] else None,
            )
            collection = _TurnCollection(record)
            client = SimpleNamespace(collection=lambda name: collection)
            monkeypatch.setattr("deeptutor.services.session.pocketbase_store._pb", lambda: client)
            store = PocketBaseSessionStore()
            # Keep the turn lookup/serialization real; owner visibility is
            # covered separately by test_pocketbase_isolation.
            monkeypatch.setattr(
                store, "get_session", AsyncMock(return_value={"id": "test-session"})
            )
        return store, "test-turn"

    return make


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        pytest.param(source, target, ALLOWED_EDGES[i][j], id=f"{source}-to-{target}")
        for i, source in enumerate(STATES)
        for j, target in enumerate(STATES)
    ],
)
@pytest.mark.asyncio
async def test_store_enforces_transition_matrix(make_turn, source, target, allowed):
    store, turn_id = await make_turn(source)
    before = await store.get_turn(turn_id)

    assert can_transition_turn(source, target) is allowed
    assert (
        await store.transition_turn(
            turn_id,
            target,
            expected_status=source,
            fencing_token=7,
            error="diagnostic",
            failure_code="worker_lost",
            retryable=True,
        )
        is allowed
    )

    after = await store.get_turn(turn_id)
    if not allowed:
        assert after == before, "a rejected edge must not modify state, timestamps or metadata"
    else:
        assert after["status"] == target
        assert after["state_version"] == before["state_version"] + 1
        assert after["error"] == "diagnostic"
        assert after["failure_code"] == "worker_lost"
        assert after["retryable"] is True
        assert (after["finished_at"] is not None) is (target in STATES[3:])


@pytest.mark.parametrize("status", STATES)
@pytest.mark.asyncio
async def test_guards_also_apply_to_same_state_writes(make_turn, status):
    store, turn_id = await make_turn(status)
    before = await store.get_turn(turn_id)
    assert not await store.transition_turn(turn_id, status, fencing_token=6)
    mismatch = "waiting_input" if status == "running" else "running"
    assert not await store.transition_turn(turn_id, status, expected_status=mismatch)
    assert await store.get_turn(turn_id) == before


@pytest.mark.asyncio
async def test_unknown_status_and_missing_turn_do_not_write(make_turn):
    store, turn_id = await make_turn()
    before = await store.get_turn(turn_id)
    with pytest.raises(ValueError, match="Unsupported turn status"):
        await store.transition_turn(turn_id, "paused")
    assert not await store.transition_turn("missing-turn", "completed")
    assert await store.get_turn(turn_id) == before


@pytest.mark.asyncio
async def test_legacy_update_entry_point_rejects_queue_rollback(make_turn):
    store, turn_id = await make_turn()
    before = await store.get_turn(turn_id)
    assert not await store.update_turn_status(turn_id, "queued")
    assert await store.get_turn(turn_id) == before


@pytest.mark.asyncio
async def test_wait_resume_and_cancel_remain_supported(make_turn):
    store, turn_id = await make_turn()
    assert await store.transition_turn(turn_id, "waiting_input", expected_status="running")
    assert await store.transition_turn(turn_id, "running", expected_status="waiting_input")
    assert await store.transition_turn(turn_id, "waiting_input", expected_status="running")
    assert await store.transition_turn(turn_id, "cancelled", expected_status="waiting_input")
    # A delayed ask_user finally block must not revive a cancelled turn.
    before = await store.get_turn(turn_id)
    assert not await store.transition_turn(turn_id, "running")
    assert await store.get_turn(turn_id) == before


def test_coordination_reexports_the_shared_enum():
    assert CoordinationTurnStatus is TurnStatus
    assert tuple(status.value for status in TurnStatus) == STATES
    assert not can_transition_turn("unknown", "running")
    assert not can_transition_turn("running", "unknown")


@pytest.mark.asyncio
async def test_sqlite_competing_terminal_writes_have_one_winner(tmp_path):
    path = tmp_path / "race.db"
    store = SQLiteSessionStore(path)
    session = await store.ensure_session(None)
    turn = await store.begin_turn(session["id"], fencing_token=7)
    stores = [SQLiteSessionStore(path) for _ in range(3)]

    results = await asyncio.gather(
        *(
            contender.transition_turn(
                turn["id"], target, expected_status="running", fencing_token=7
            )
            for contender, target in zip(stores, STATES[3:], strict=True)
        )
    )

    assert sum(results) == 1
    persisted = await store.get_turn(turn["id"])
    assert persisted["status"] == STATES[3:][results.index(True)]
    assert persisted["state_version"] == 2
