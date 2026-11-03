"""Implements specs/booking-state-machine.md. Keep both in sync (ADR-6)."""
from enum import Enum


class State(str, Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    STORED = "STORED"
    OVERSTAY = "OVERSTAY"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


TRANSITIONS: dict[tuple[State, str], State] = {
    (State.CREATED, "payment_captured"): State.PAID,
    (State.CREATED, "expire"): State.EXPIRED,
    (State.CREATED, "cancel"): State.CANCELLED,
    (State.PAID, "door_closed"): State.STORED,
    (State.PAID, "expire"): State.EXPIRED,
    (State.STORED, "time_exceeded"): State.OVERSTAY,
    (State.STORED, "pickup_completed"): State.RELEASED,
    (State.OVERSTAY, "pickup_completed"): State.RELEASED,
    (State.STORED, "dispute"): State.DISPUTED,
    (State.OVERSTAY, "dispute"): State.DISPUTED,
    (State.DISPUTED, "resolve"): State.RELEASED,
}

TERMINAL = {State.RELEASED, State.EXPIRED, State.CANCELLED}
UNLOCKABLE = {State.PAID, State.STORED, State.OVERSTAY}  # REQ-LOCK-001


class InvalidTransition(Exception):
    pass


def apply(state: State, event: str) -> State:
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise InvalidTransition(f"{event!r} not allowed from {state.value}")


def would_replay(state: State, event: str) -> bool:
    """True if `event` already produced `state` (safe to ignore duplicates)."""
    return any(to == state and ev == event for (_, ev), to in TRANSITIONS.items())
