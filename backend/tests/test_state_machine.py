import itertools
import pytest

from app.state_machine import (State, InvalidTransition, TERMINAL, TRANSITIONS, UNLOCKABLE,
                               apply, would_replay)


@pytest.mark.req("REQ-PAY-001")
def test_payment_moves_created_to_paid():
    assert apply(State.CREATED, "payment_captured") == State.PAID


@pytest.mark.req("REQ-LOCK-003")
def test_door_closed_moves_paid_to_stored():
    assert apply(State.PAID, "door_closed") == State.STORED


@pytest.mark.req("REQ-LOCK-004")
def test_pickup_releases_from_stored_and_overstay():
    assert apply(State.STORED, "pickup_completed") == State.RELEASED
    assert apply(State.OVERSTAY, "pickup_completed") == State.RELEASED


@pytest.mark.req("REQ-BOOK-004")
def test_unpaid_booking_can_expire():
    assert apply(State.CREATED, "expire") == State.EXPIRED


def test_illegal_transitions_raise():
    events = {e for (_, e) in TRANSITIONS}
    for state, event in itertools.product(State, events):
        if (state, event) not in TRANSITIONS:
            with pytest.raises(InvalidTransition):
                apply(state, event)


def test_terminal_states_have_no_exits():
    assert not any(s in TERMINAL for (s, _) in TRANSITIONS if s != State.DISPUTED)


@pytest.mark.req("REQ-LOCK-002")
def test_unlock_only_in_active_states():
    assert UNLOCKABLE == {State.PAID, State.STORED, State.OVERSTAY}


@pytest.mark.req("REQ-PAY-003")
def test_replayed_event_is_detected():
    assert would_replay(State.PAID, "payment_captured")
    assert not would_replay(State.CREATED, "payment_captured")


def test_code_matches_state_machine_spec():
    """The markdown table in specs is the source of truth."""
    import re
    from tests.conftest import SPECS
    text = (SPECS / "booking-state-machine.md").read_text()
    spec = set()
    for row in re.findall(r"^\|\s*([A-Z /]+?)\s*\|\s*(\w+)\s*\|\s*([A-Z]+)\s*\|", text, re.M):
        frm, ev, to = row
        for s in (x.strip() for x in frm.split("/")):
            if s in State.__members__:
                spec.add((State[s], ev, State[to]))
    code = {(f, e, t) for (f, e), t in TRANSITIONS.items()}
    assert spec == code
