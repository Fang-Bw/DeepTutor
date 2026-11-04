"""Persisted turn states and legal transitions, independent of any backend.

New turns currently start at ``running``. ``queued`` remains supported for
existing records and admission integrations, but an executing turn cannot be
put back in the queue. Repeating a state is allowed so callers can update its
metadata; this is not an instruction to restart a terminal turn.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_TURN_STATUSES = frozenset(
    {TurnStatus.QUEUED.value, TurnStatus.RUNNING.value, TurnStatus.WAITING_INPUT.value}
)
TERMINAL_TURN_STATUSES = frozenset(
    {TurnStatus.COMPLETED.value, TurnStatus.FAILED.value, TurnStatus.CANCELLED.value}
)
ALL_TURN_STATUSES = ACTIVE_TURN_STATUSES | TERMINAL_TURN_STATUSES

TURN_STATUS_TRANSITIONS = MappingProxyType(
    {
        TurnStatus.QUEUED.value: frozenset({"queued", "running"}) | TERMINAL_TURN_STATUSES,
        TurnStatus.RUNNING.value: frozenset({"running", "waiting_input"}) | TERMINAL_TURN_STATUSES,
        TurnStatus.WAITING_INPUT.value: frozenset({"running", "waiting_input"})
        | TERMINAL_TURN_STATUSES,
        **{status: frozenset({status}) for status in TERMINAL_TURN_STATUSES},
    }
)


def can_transition_turn(current_status: str, target_status: str) -> bool:
    """Check the state edge; ownership and expected-state guards remain separate.

    Unknown states are rejected. Stores additionally raise ``ValueError`` for
    an unknown target, preserving their existing public validation contract.
    This predicate performs no I/O and provides no concurrency guarantees.
    """
    return target_status in TURN_STATUS_TRANSITIONS.get(current_status, ())
