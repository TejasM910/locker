"""Tests for edge cases documented in EDGE_CASES_AND_SOLUTIONS.md."""

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.models import State
from app.routers import bookings as br
from app.schemas import BookingCreate


class FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val

    def scalar_one(self):
        return self._val

    def first(self):
        return self._val

    def scalar(self):
        return self._val

    def scalars(self):
        return self


@pytest.mark.asyncio
async def test_active_hold_prevents_hoarding(monkeypatch):
    """REQ-BOOK-008: Users cannot hoard multiple unpaid locker holds."""
    locker = SimpleNamespace(
        id=1, location_id=1, size="M", locker_code="TEST101",
        device_id="test-dev-1", status="free"
    )
    user = SimpleNamespace(id=1)
    existing_unpaid_hold = SimpleNamespace(id=10, state=State.CREATED)

    class FakeSession:
        def begin(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, m, k):
            if m is br.User:
                return user
            return None

        async def execute(self, q):
            qs = str(q)
            if "count" in qs.lower() and "bookings" in qs.lower():
                return FakeResult(1)
            return FakeResult(locker)

    with pytest.raises(HTTPException) as exc:
        await br.create_booking(
            BookingCreate(locker_code="TEST101", duration_hours=1),
            user_id=1,
            session=FakeSession(),
            idempotency_key=None,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "ACTIVE_HOLD_EXISTS"


@pytest.mark.asyncio
async def test_tamper_event_marks_locker_offline():
    """REQ-LOCK-007: Device hardware tamper event marks the locker unit offline."""
    from app.device_events import handle_device_event

    locker = SimpleNamespace(id=1, device_id="esp32-lock-1", status="free")

    class FakeDeviceSession:
        def begin(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def execute(self, q):
            return FakeResult(locker)

        def add(self, item):
            pass

    await handle_device_event(FakeDeviceSession(), "esp32-lock-1", {"evt": "tamper_detected"})
    assert locker.status == "offline"


@pytest.mark.asyncio
async def test_door_jam_marks_locker_offline():
    """REQ-LOCK-007: Mechanical door jam event marks the locker unit offline."""
    from app.device_events import handle_device_event

    locker = SimpleNamespace(id=2, device_id="esp32-lock-2", status="held")

    class FakeDeviceSession:
        def begin(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def execute(self, q):
            return FakeResult(locker)

        def add(self, item):
            pass

    await handle_device_event(FakeDeviceSession(), "esp32-lock-2", {"evt": "err:door_jammed"})
    assert locker.status == "offline"
