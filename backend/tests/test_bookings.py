from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import bookings as booking_router
from app.schemas import BookingCreate
from app.state_machine import State


def test_booking_timestamps_are_serialized_as_utc():
    timestamp = datetime(2026, 11, 9, 12, 0)

    assert booking_router.as_utc(timestamp) == timestamp.replace(tzinfo=timezone.utc)
    assert booking_router.as_utc(None) is None


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value

    def scalar_one(self):
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
        self.booking = None

    def begin(self):
        return FakeTransaction()

    async def get(self, model, _key):
        if model is booking_router.User:
            return self.user
        if model is booking_router.Booking:
            return self.booking
        return None

    async def execute(self, query):
        query_text = str(query)
        if "booking_requests" in query_text:
            records = [item for item in self.added if isinstance(item, booking_router.BookingRequest)]
            return FakeResult(records[0] if records else None)
        if "utc_timestamp" in query_text:
            return FakeResult(datetime(2026, 11, 9, 12, 0))
        if "FROM lockers" in query_text:
            return FakeResult(self.locker if self.locker.status == "free" else None)
        if "FROM bookings" in query_text:
            return FakeResult(self.booking)
        return FakeResult(self.locker if self.locker.status == "free" else None)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        for item in self.added:
            if isinstance(item, booking_router.BookingRequest) and item.id is None:
                item.id = 1
            if isinstance(item, booking_router.Booking) and item.id is None:
                item.id = 12
                item.public_id = "booking-public-id"
                item.state = State.CREATED
                item.locker = self.locker
                self.booking = item


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
        idempotency_key=None,
    )

    assert locker.status == "held"
    assert response.locker_code == "CH101"
    assert response.size == "L"
    assert response.amount_paise == booking_router.settings.price_per_hour_paise["L"] * 2
    assert response.razorpay_order_id == "order-123"
    assert session.booking.razorpay_order_id == "order-123"


@pytest.mark.asyncio
async def test_booking_idempotency_key_returns_original_booking(monkeypatch):
    locker = SimpleNamespace(
        id=11,
        location_id=4,
        size="L",
        locker_code="CH101",
        device_id="church-device-1",
        status="free",
    )
    session = FakeBookingSession(SimpleNamespace(id=8), locker)
    order_calls = 0

    async def create_order(_amount_paise, _public_id):
        nonlocal order_calls
        order_calls += 1
        return "order-123"

    monkeypatch.setattr(booking_router.payments, "create_order", create_order)
    body = BookingCreate(locker_code="CH101", duration_hours=2)
    first = await booking_router.create_booking(body, user_id=8, session=session, idempotency_key="request-123")
    replay = await booking_router.create_booking(
        body, user_id=8, session=session, idempotency_key="request-123"
    )

    assert replay.public_id == first.public_id
    assert order_calls == 1


@pytest.mark.asyncio
async def test_booking_idempotency_key_rejects_different_request(monkeypatch):
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
    await booking_router.create_booking(
        BookingCreate(locker_code="CH101", duration_hours=2),
        user_id=8,
        session=session,
        idempotency_key="request-123",
    )

    with pytest.raises(HTTPException) as error:
        await booking_router.create_booking(
            BookingCreate(locker_code="CH101", duration_hours=1),
            user_id=8,
            session=session,
            idempotency_key="request-123",
        )

    assert error.value.status_code == 409
    assert error.value.detail == "IDEMPOTENCY_KEY_REUSED"


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
            idempotency_key=None,
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
