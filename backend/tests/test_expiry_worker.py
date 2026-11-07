from datetime import timedelta

import asyncio
import pytest

from app.models import Booking, Locker, LockerEvent, utcnow
from app.state_machine import State


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, booking, locker):
        self.booking = booking
        self.locker = locker
        self.events = []

    async def execute(self, *_args, **_kwargs):
        return FakeResult([self.booking])

    async def get(self, model, pk):
        if model is Locker and pk == self.locker.id:
            return self.locker
        return None

    def add(self, item):
        self.events.append(item)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_expire_unpaid_booking_releases_locker():
    from app.booking_expiry import expire_unpaid_bookings

    locker = Locker(
        id=1,
        location_id=1,
        size="M",
        locker_code="CE101",
        device_id="locker-1",
        status="held",
    )
    booking = Booking(
        id=1,
        user_id=1,
        locker_id=1,
        state=State.CREATED,
        amount_paise=4000,
        duration_hours=1,
        created_at=utcnow(),
        expires_at=utcnow() - timedelta(minutes=1),
        public_id="abc123",
    )
    session = FakeSession(booking, locker)

    count = await expire_unpaid_bookings(session)

    assert count == 1
    assert booking.state == State.EXPIRED
    assert locker.status == "free"
    assert len(session.events) == 1
    assert isinstance(session.events[0], LockerEvent)
    assert session.events[0].type == "booking_expired"


@pytest.mark.asyncio
async def test_expiry_worker_runs_in_committing_transaction(monkeypatch):
    from app import booking_expiry

    transaction = {"entered": False, "exited": False, "exception": False}

    class TransactionSession:
        async def __aenter__(self):
            transaction["entered"] = True
            return self

        async def __aexit__(self, exc_type, _exc, _traceback):
            transaction["exited"] = True
            transaction["exception"] = exc_type is not None

    class TransactionFactory:
        def begin(self):
            return TransactionSession()

    async def expire(_session):
        return 1

    monkeypatch.setattr(booking_expiry, "SessionLocal", TransactionFactory())
    monkeypatch.setattr(booking_expiry, "expire_unpaid_bookings", expire)

    assert await booking_expiry.expire_unpaid_bookings_transactionally() == 1
    assert transaction == {"entered": True, "exited": True, "exception": False}
