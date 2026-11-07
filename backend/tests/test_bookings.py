from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import bookings as booking_router
from app.schemas import BookingCreate
from app.state_machine import State


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class FakeBookingSession:
    def __init__(self, user, locker):
        self.user = user
        self.locker = locker
        self.added = []

    def begin(self):
        return FakeTransaction()

    async def get(self, _model, _key):
        return self.user

    async def execute(self, _query):
        assert "locker_code" in str(_query)
        return FakeResult(self.locker if self.locker.status == "free" else None)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        booking = self.added[-1]
        booking.public_id = "booking-public-id"
        booking.state = State.CREATED
        booking.locker = self.locker


@pytest.mark.asyncio
async def test_booking_reserves_exact_selected_locker_code(monkeypatch):
    locker = SimpleNamespace(
        id=11,
        location_id=4,
        size="L",
        locker_code="CH101",
        device_id="church-device-1",
        status="free",
    )
    session = FakeBookingSession(SimpleNamespace(id=8), locker)

    async def create_order(_amount_paise, _public_id):
        return "order-123"

    monkeypatch.setattr(booking_router.payments, "create_order", create_order)
    response = await booking_router.create_booking(
        BookingCreate(locker_code="CH101", duration_hours=2),
        user_id=8,
        session=session,
    )

    assert locker.status == "held"
    assert response.locker_code == "CH101"
    assert response.size == "L"
    assert response.amount_paise == booking_router.settings.price_per_hour_paise["L"] * 2
    assert response.razorpay_order_id == "order-123"


@pytest.mark.asyncio
async def test_booking_does_not_substitute_when_selected_code_is_unavailable(monkeypatch):
    locker = SimpleNamespace(
        id=11,
        location_id=4,
        size="L",
        locker_code="CH101",
        device_id="church-device-1",
        status="held",
    )
    session = FakeBookingSession(SimpleNamespace(id=8), locker)

    with pytest.raises(HTTPException) as error:
        await booking_router.create_booking(
            BookingCreate(locker_code="CH101", duration_hours=1),
            user_id=8,
            session=session,
        )

    assert error.value.status_code == 409
    assert error.value.detail == "NO_LOCKER_AVAILABLE"
    assert session.added == []


def test_booking_request_requires_a_specific_code_or_legacy_location_and_size():
    with pytest.raises(ValueError):
        BookingCreate(duration_hours=1)

    legacy = BookingCreate(location_id=4, size="M", duration_hours=1)
    assert legacy.location_id == 4
    assert legacy.locker_code is None
