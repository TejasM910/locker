"""Booking domain state machine and rules (SRP/Pure Domain)."""

from datetime import datetime, timezone
from app.models import State

# Transition validation
VALID_TRANSITIONS = {
    State.CREATED: {State.PAID, State.EXPIRED, State.CANCELLED},
    State.PAID: {State.STORED, State.RELEASED},
    State.STORED: {State.RELEASED, State.OVERSTAY},
    State.OVERSTAY: {State.RELEASED, State.DISPUTED},
    State.DISPUTED: {State.RELEASED},
    State.EXPIRED: set(),
    State.CANCELLED: set(),
    State.RELEASED: set(),
}


def can_transition(current: State, target: State) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
