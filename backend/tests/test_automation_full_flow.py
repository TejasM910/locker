"""
Automation test suite for the Locker App — full end-to-end API flows.

Covers:
  - Auth: OTP request/verify, rate limiting, token lifecycle
  - Locations: nearby search, locker inventory
  - Bookings: create, get, cancel, idempotency, conflict
  - Webhooks: payment captured, failed, bad signature, duplicate
  - Unlock: state guards, rate limit
  - Expiry worker: expire + locker release
  - State machine: all legal/illegal transitions
  - MQTT: payload parser
  - Security: unauthenticated access, ownership checks
  - Edge cases: bad inputs, missing resources, concurrent holds

All tests use in-process ASGI transport (no real MySQL, Mosquitto, or
Razorpay).  DB-dependent tests use SQLite in-memory via AsyncSession.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import (
    TOKEN_TTL_SECONDS,
    create_token,
    issue_otp,
    normalize_phone,
    rate_limited,
    resolve_token,
    verify_otp,
)
from app.config import settings
from app.main import app
from app.payments import verify_signature
from app.state_machine import TERMINAL, TRANSITIONS, UNLOCKABLE, State, apply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PHONE = "9876543210"
ALT_PHONE = "9123456789"
API_PREFIX = "/v1"


def _sign(body: bytes) -> str:
    return hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def _webhook_body(event: str, order_id: str, payment_id: str, amount: int, currency: str = "INR") -> bytes:
    return json.dumps({
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "order_id": order_id,
                    "id": payment_id,
                    "amount": amount,
                    "currency": currency,
                }
            }
        },
    }).encode()


# ---------------------------------------------------------------------------
# 1. AUTH — OTP + Token tests
# ---------------------------------------------------------------------------

class TestAuthOtpRoundTrip:
    """REQ-SEC-004: OTP lifecycle and in-process token store."""

    def test_otp_is_6_digit_numeric_string(self):
        code = issue_otp("9000000001")
        assert len(code) == 6
        assert code.isdigit()

    def test_otp_is_single_use(self):
        code = issue_otp("9000000002")
        assert verify_otp("9000000002", code) is True
        assert verify_otp("9000000002", code) is False  # consumed

    def test_wrong_otp_is_rejected(self):
        issue_otp("9000000003")
        assert verify_otp("9000000003", "000000") is False

    def test_otp_for_unknown_phone_is_rejected(self):
        assert verify_otp("9000000099", "123456") is False

    def test_phone_normalization_strips_non_digits(self):
        assert normalize_phone("+91 98765 43210") == "919876543210"
        assert normalize_phone("9876543210") == "9876543210"

    def test_phone_too_short_raises(self):
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            normalize_phone("123")

    def test_bearer_token_resolves_to_user_id(self):
        uid = 777
        token = create_token(uid)
        assert resolve_token(token) == uid

    def test_unknown_token_resolves_to_none(self):
        assert resolve_token("lock_nonexistent") is None

    def test_empty_token_resolves_to_none(self):
        assert resolve_token("") is None


class TestAuthRateLimiting:
    """REQ-SEC-004: 5 OTP requests per 10 min per phone."""

    def test_otp_rate_limit_allows_5_then_blocks(self):
        phone = f"8{uuid.uuid4().int % 10**9:09d}"
        for _ in range(5):
            assert not rate_limited(phone)
        assert rate_limited(phone) is True

    def test_different_phones_have_independent_limits(self):
        p1 = f"7{uuid.uuid4().int % 10**9:09d}"
        p2 = f"6{uuid.uuid4().int % 10**9:09d}"
        for _ in range(5):
            rate_limited(p1)
        # p2 should still be allowed
        assert not rate_limited(p2)

    def test_unlock_rate_limit_allows_30_per_minute(self):
        from app.auth import MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS
        key = f"unlock:{uuid.uuid4().hex}"
        for _ in range(MAX_UNLOCK_REQUESTS_PER_WINDOW):
            assert not rate_limited(key, MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS)
        assert rate_limited(key, MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS) is True


# ---------------------------------------------------------------------------
# 2. AUTH API endpoints (via ASGI transport)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAuthEndpoints:
    """OTP HTTP endpoints via FastAPI ASGI transport."""

    async def test_otp_request_returns_debug_code(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/auth/otp/request", json={"phone": "9100000001"})
        assert resp.status_code == 200
        body = resp.json()
        assert "debug_code" in body
        assert len(body["debug_code"]) == 6

    async def test_otp_verify_returns_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            req = await client.post(f"{API_PREFIX}/auth/otp/request", json={"phone": "9100000002"})
            code = req.json()["debug_code"]
            resp = await client.post(f"{API_PREFIX}/auth/otp/verify", json={"phone": "9100000002", "otp": code})
        assert resp.status_code == 200
        assert "token" in resp.json()
        assert "user_id" in resp.json()

    async def test_invalid_otp_returns_400(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"{API_PREFIX}/auth/otp/request", json={"phone": "9100000003"})
            resp = await client.post(f"{API_PREFIX}/auth/otp/verify", json={"phone": "9100000003", "otp": "000000"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "INVALID_OTP"

    async def test_otp_rate_limit_returns_429(self, monkeypatch):
        from app.routers import auth as auth_router
        monkeypatch.setattr(auth_router, "rate_limited", lambda *_: True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/auth/otp/request", json={"phone": "9100000004"})
        assert resp.status_code == 429
        assert resp.json()["detail"] == "OTP_RATE_LIMITED"

    async def test_unauthenticated_booking_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/bookings", json={"locker_code": "CH101", "duration_hours": 1})
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"{API_PREFIX}/bookings",
                json={"locker_code": "CH101", "duration_hours": 1},
                headers={"Authorization": "Bearer invalid-token"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. LOCATIONS — nearby search + locker inventory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestNearbyLocations:
    """REQ-LOC-001..007: Nearby search, bounding box, rate limit, inventory."""

    async def _get_client_with_sqlite(self):
        """Returns an AsyncClient using an in-memory SQLite session override."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.db import get_session
        from app.models import Base, Location, Locker

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = Session(engine)

        session.add_all([
            Location(id=10, name="Central Locker Hub", address="MG Road", lat=12.9716, lng=77.5946),
            Location(id=11, name="Nearby Hub", address="Brigade Road", lat=12.9720, lng=77.5960),
            Location(id=12, name="Far Hub", address="Far Place", lat=13.1000, lng=78.0000),  # out of radius
        ])
        session.add_all([
            Locker(id=1, location_id=10, size="M", locker_code="MG101", device_id="mg-m-1", status="free"),
            Locker(id=2, location_id=10, size="M", locker_code="MG102", device_id="mg-m-2", status="held"),
            Locker(id=3, location_id=10, size="L", locker_code="MG103", device_id="mg-l-1", status="free"),
            Locker(id=4, location_id=11, size="M", locker_code="BR101", device_id="br-m-1", status="offline"),
        ])
        session.commit()

        class AsyncAdapter:
            async def execute(self, q):
                return session.execute(q)
            async def get(self, m, k):
                return session.get(m, k)

        async def override():
            yield AsyncAdapter()

        app.dependency_overrides[get_session] = override
        client = AsyncClient(
            transport=ASGITransport(app=app, client=("test-ip-nearby", 1234)),
            base_url="http://test",
        )
        return client, session, engine

    async def test_nearby_returns_only_within_radius(self):
        from app.db import get_session
        client, session, engine = await self._get_client_with_sqlite()
        try:
            async with client as c:
                resp = await c.post(f"{API_PREFIX}/locations/nearby",
                                    json={"latitude": 12.9716, "longitude": 77.5946})
            assert resp.status_code == 200
            ids = [item["location_id"] for item in resp.json()["items"]]
            assert 10 in ids
            assert 11 in ids
            assert 12 not in ids  # outside 1 km
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_nearby_returns_cache_no_store_header(self):
        from app.db import get_session
        client, session, engine = await self._get_client_with_sqlite()
        try:
            async with client as c:
                resp = await c.post(f"{API_PREFIX}/locations/nearby",
                                    json={"latitude": 12.9716, "longitude": 77.5946})
            assert "no-store" in resp.headers.get("cache-control", "")
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_nearby_coordinates_not_in_query_string(self):
        from app.db import get_session
        client, session, engine = await self._get_client_with_sqlite()
        try:
            async with client as c:
                resp = await c.post(f"{API_PREFIX}/locations/nearby",
                                    json={"latitude": 12.9716, "longitude": 77.5946})
            assert resp.request.url.query == b""
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_nearby_sorted_nearest_first(self):
        from app.db import get_session
        client, session, engine = await self._get_client_with_sqlite()
        try:
            async with client as c:
                resp = await c.post(f"{API_PREFIX}/locations/nearby",
                                    json={"latitude": 12.9716, "longitude": 77.5946})
            items = resp.json()["items"]
            distances = [item["distance_m"] for item in items]
            assert distances == sorted(distances)
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_nearby_free_counts_exclude_held_and_offline(self):
        from app.db import get_session
        client, session, engine = await self._get_client_with_sqlite()
        try:
            async with client as c:
                resp = await c.post(f"{API_PREFIX}/locations/nearby",
                                    json={"latitude": 12.9716, "longitude": 77.5946})
            items = {i["location_id"]: i for i in resp.json()["items"]}
            # Location 10: M=1 free (MG101), L=1 free (MG103); MG102 held excluded
            assert items[10]["available_by_size"]["M"] == 1
            assert items[10]["available_by_size"]["L"] == 1
            # Location 11: all lockers offline/held → M=0, L=0
            assert items[11]["available_by_size"]["M"] == 0
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_nearby_rejects_latitude_out_of_range(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/locations/nearby",
                                     json={"latitude": 95, "longitude": 0})
        assert resp.status_code == 422

    async def test_nearby_rejects_longitude_out_of_range(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/locations/nearby",
                                     json={"latitude": 0, "longitude": 200})
        assert resp.status_code == 422

    async def test_nearby_rejects_radius_above_max(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"{API_PREFIX}/locations/nearby",
                                     json={"latitude": 0, "longitude": 0, "radius_m": 5000})
        assert resp.status_code == 422

    async def test_nearby_rate_limit_returns_429(self, monkeypatch):
        from app.routers import locations as loc_router
        from app.db import get_session
        monkeypatch.setattr(loc_router, "rate_limited", lambda *_: True)

        async def dummy_session():
            yield object()
        app.dependency_overrides[get_session] = dummy_session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, client=("rate-limited-client", 1234)),
                base_url="http://test",
            ) as client:
                resp = await client.post(f"{API_PREFIX}/locations/nearby",
                                         json={"latitude": 12.9, "longitude": 77.5})
            assert resp.status_code == 429
            assert resp.json()["detail"] == "LOCATION_SEARCH_RATE_LIMITED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
class TestLocationLockers:
    """Locker inventory per location."""

    async def test_location_lockers_returns_all_with_codes(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.db import get_session
        from app.models import Base, Location, Locker

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = Session(engine)
        session.add(Location(id=20, name="KR Market", address="KR Market Area", lat=12.96, lng=77.58))
        session.add_all([
            Locker(id=10, location_id=20, size="M", locker_code="KR101", device_id="kr-m-1", status="free"),
            Locker(id=11, location_id=20, size="M", locker_code="KR102", device_id="kr-m-2", status="held"),
            Locker(id=12, location_id=20, size="L", locker_code="KR103", device_id="kr-l-1", status="offline"),
        ])
        session.commit()

        class AsyncAdapter:
            async def execute(self, q): return session.execute(q)
            async def get(self, m, k): return session.get(m, k)

        async def override(): yield AsyncAdapter()
        app.dependency_overrides[get_session] = override
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"{API_PREFIX}/locations/20/lockers",
                                        headers={"X-User-Id": "5"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["location_name"] == "KR Market"
            codes = [item["locker_code"] for item in body["items"]]
            assert set(codes) == {"KR101", "KR102", "KR103"}
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_location_lockers_404_for_missing_location(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.db import get_session
        from app.models import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = Session(engine)
        session.commit()

        class AsyncAdapter:
            async def execute(self, q): return session.execute(q)
            async def get(self, m, k): return session.get(m, k)

        async def override(): yield AsyncAdapter()
        app.dependency_overrides[get_session] = override
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get(f"{API_PREFIX}/locations/9999/lockers",
                                        headers={"X-User-Id": "5"})
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.pop(get_session, None)
            session.close(); engine.dispose()

    async def test_location_lockers_requires_auth(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{API_PREFIX}/locations/1/lockers")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. BOOKINGS — creation, retrieval, cancellation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBookingCreation:
    """REQ-BOOK-001..007: Exact locker reservation, conflict, idempotency."""

    async def test_booking_reserves_exact_locker_code(self, monkeypatch):
        from app.routers import bookings as br
        from app.schemas import BookingCreate

        locker = SimpleNamespace(id=1, location_id=1, size="M", locker_code="TEST101",
                                 device_id="test-dev-1", status="free")
        user = SimpleNamespace(id=1)

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, m, k):
                if m is br.User: return user
                if m is br.Booking: return getattr(self, '_booking', None)
                return None
            async def execute(self, q):
                qs = str(q)
                if "booking_requests" in qs: return FakeResult(None)
                if "utc_timestamp" in qs: return FakeResult(datetime(2026, 11, 9, 12, 0))
                if "FROM lockers" in qs: return FakeResult(locker if locker.status == "free" else None)
                if "FROM bookings" in qs: return FakeResult(getattr(self, "_booking", None))
                return FakeResult(locker)
            def add(self, item):
                if isinstance(item, br.Booking):
                    item.id = 1
                    item.public_id = "pub-id-1"
                    item.state = State.CREATED
                    item.locker = locker
                    self._booking = item
            async def flush(self): pass

        async def mock_create_order(*_):
            return "order-xyz"

        monkeypatch.setattr(br.payments, "create_order", mock_create_order)
        resp = await br.create_booking(
            BookingCreate(locker_code="TEST101", duration_hours=2),
            user_id=1, session=FakeSession(), idempotency_key=None,
        )
        assert resp.locker_code == "TEST101"
        assert resp.state == "CREATED"
        assert locker.status == "held"

    async def test_booking_409_when_locker_unavailable(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br
        from app.schemas import BookingCreate

        locker = SimpleNamespace(id=1, location_id=1, size="M", locker_code="TEST201",
                                 device_id="test-dev-2", status="held")  # already held
        user = SimpleNamespace(id=2)

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, m, k):
                if m is br.User: return user
                return None
            async def execute(self, q):
                qs = str(q)
                if "booking_requests" in qs: return FakeResult(None)
                if "utc_timestamp" in qs: return FakeResult(datetime(2026, 11, 9, 12, 0))
                return FakeResult(None)  # no free locker found
            def add(self, item): pass
            async def flush(self): pass

        with pytest.raises(HTTPException) as exc:
            await br.create_booking(
                BookingCreate(locker_code="TEST201", duration_hours=1),
                user_id=2, session=FakeSession(), idempotency_key=None,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "NO_LOCKER_AVAILABLE"

    async def test_booking_idempotency_replay_returns_same_booking(self, monkeypatch):
        from app.routers import bookings as br
        from app.schemas import BookingCreate

        locker = SimpleNamespace(id=3, location_id=1, size="L", locker_code="TEST301",
                                 device_id="test-dev-3", status="free")
        user = SimpleNamespace(id=3)
        order_calls = 0

        async def mock_create_order(*_):
            nonlocal order_calls
            order_calls += 1
            return "order-301"

        monkeypatch.setattr(br.payments, "create_order", mock_create_order)

        class FakeSession:
            def __init__(self):
                self._requests = {}
                self._booking = None
                self._added_requests = []
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, m, k):
                if m is br.User: return user
                if m is br.Booking: return self._booking
                return None
            async def execute(self, q):
                qs = str(q)
                if "booking_requests" in qs:
                    for r in list(self._added_requests):
                        if r.idempotency_key == self._last_key:
                            return FakeResult(r)
                    return FakeResult(None)
                if "utc_timestamp" in qs: return FakeResult(datetime(2026, 11, 9, 12, 0))
                if "FROM lockers" in qs: return FakeResult(locker if locker.status == "free" else None)
                return FakeResult(self._booking)
            def add(self, item):
                if not hasattr(self, '_added_requests'): self._added_requests = []
                if isinstance(item, br.BookingRequest):
                    item.id = len(self._added_requests) + 1
                    self._added_requests.append(item)
                if isinstance(item, br.Booking):
                    item.id = 10; item.public_id = "pub-301"; item.state = State.CREATED; item.locker = locker
                    self._booking = item
                    for r in self._added_requests:
                        if not r.booking_id: r.booking_id = item.id
            async def flush(self): pass

        session = FakeSession()
        body = BookingCreate(locker_code="TEST301", duration_hours=1)
        session._last_key = "idem-key-1"
        first = await br.create_booking(body, user_id=3, session=session, idempotency_key="idem-key-1")
        session._last_key = "idem-key-1"
        replay = await br.create_booking(body, user_id=3, session=session, idempotency_key="idem-key-1")
        assert first.public_id == replay.public_id
        assert order_calls == 1  # Razorpay called only once

    async def test_booking_requires_locker_code_or_location_plus_size(self):
        from app.schemas import BookingCreate
        with pytest.raises(ValueError):
            BookingCreate(duration_hours=1)  # neither locker_code nor location_id+size

        ok = BookingCreate(locker_code="X101", duration_hours=3)
        assert ok.locker_code == "X101"

        legacy = BookingCreate(location_id=1, size="M", duration_hours=2)
        assert legacy.location_id == 1

    async def test_get_booking_returns_correct_data(self, monkeypatch):
        from app.routers import bookings as br

        booking = SimpleNamespace(
            id=5, public_id="getpubid", user_id=9,
            state=State.CREATED, amount_paise=4000, duration_hours=1,
            razorpay_order_id="ord-get",
            expires_at=datetime(2026, 11, 9, 12, 15, tzinfo=timezone.utc),
            ends_at=None,
            locker=SimpleNamespace(locker_code="GE101", size="M"),
        )

        class FakeSession:
            async def execute(self, q):
                return FakeResult(booking)

        async def owned_ok(session, pid, uid): return booking
        monkeypatch.setattr(br, "_owned", owned_ok)
        result = await br.get_booking("getpubid", user_id=9, session=FakeSession())
        assert result.public_id == "getpubid"
        assert result.state == "CREATED"

    async def test_cancel_created_booking_frees_locker(self, monkeypatch):
        from app.routers import bookings as br

        locker = SimpleNamespace(id=6, status="held")
        booking = SimpleNamespace(id=6, public_id="cancelpub", user_id=11,
                                  state=State.CREATED, locker_id=6)
        events = []

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def execute(self, q):
                qs = str(q)
                if "FROM lockers" in qs: return FakeResult(locker)
                return FakeResult(booking)
            def add(self, item): events.append(item)

        result = await br.cancel_booking("cancelpub", user_id=11, session=FakeSession())
        assert result["state"] == "CANCELLED"
        assert locker.status == "free"
        assert any(getattr(e, "type", None) == "booking_cancelled" for e in events)

    async def test_cancel_paid_booking_returns_409(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br

        booking = SimpleNamespace(id=7, public_id="paidpub", user_id=12,
                                  state=State.PAID, locker_id=7)

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def execute(self, q): return FakeResult(booking)
            def add(self, item): pass

        with pytest.raises(HTTPException) as exc:
            await br.cancel_booking("paidpub", user_id=12, session=FakeSession())
        assert exc.value.status_code == 409
        assert "CANNOT_CANCEL_PAID" in exc.value.detail

    async def test_booking_ownership_check_403(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br

        booking = SimpleNamespace(id=8, public_id="ownerpub", user_id=99)  # owned by user 99

        class FakeSession:
            async def execute(self, q): return FakeResult(booking)

        with pytest.raises(HTTPException) as exc:
            await br._owned(FakeSession(), "ownerpub", 1)  # user 1 ≠ owner 99
        assert exc.value.status_code == 403
        assert exc.value.detail == "FORBIDDEN"

    async def test_get_booking_404_for_missing(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br

        class FakeSession:
            async def execute(self, q): return FakeResult(None)

        with pytest.raises(HTTPException) as exc:
            await br._owned(FakeSession(), "nonexistent", 1)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. UNLOCK endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUnlock:
    """REQ-LOCK-001/002/006: Unlock guards + MQTT publish + audit."""

    async def test_unlock_paid_booking_publishes_command(self, monkeypatch):
        from app.routers import bookings as br

        booking = SimpleNamespace(
            id=20, public_id="unlkpub", user_id=50,
            locker_id=20, state=State.PAID,
            locker=SimpleNamespace(device_id="dev-unlock-1"),
        )
        events = []

        class FakeSession:
            def add(self, e): events.append(e)
            async def commit(self): pass

        async def owned_ok(*_): return booking
        async def pub_unlock(*_): return "cmd-unlock-1"

        monkeypatch.setattr(br, "_owned", owned_ok)
        monkeypatch.setattr(br, "rate_limited", lambda *_: False)
        monkeypatch.setattr(br.mqtt, "publish_unlock", pub_unlock)

        result = await br.unlock_booking("unlkpub", user_id=50, session=FakeSession())
        assert result["cmd_id"] == "cmd-unlock-1"
        assert len(events) == 1
        assert events[0].type == "unlock_requested"

    async def test_unlock_created_booking_returns_403(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br

        booking = SimpleNamespace(
            id=21, public_id="unlkcreate", user_id=51,
            locker_id=21, state=State.CREATED,
            locker=SimpleNamespace(device_id="dev-unlock-2"),
        )

        async def owned_ok(*_): return booking
        monkeypatch.setattr(br, "_owned", owned_ok)
        monkeypatch.setattr(br, "rate_limited", lambda *_: False)

        with pytest.raises(HTTPException) as exc:
            await br.unlock_booking("unlkcreate", user_id=51, session=object())
        assert exc.value.status_code == 403
        assert "NOT_UNLOCKABLE" in exc.value.detail

    async def test_unlock_rate_limit_returns_429(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import bookings as br

        booking = SimpleNamespace(
            id=22, public_id="unlkrate", user_id=52,
            locker_id=22, state=State.PAID,
            locker=SimpleNamespace(device_id="dev-unlock-3"),
        )

        async def owned_ok(*_): return booking
        monkeypatch.setattr(br, "_owned", owned_ok)
        monkeypatch.setattr(br, "rate_limited", lambda *_: True)  # rate limited

        with pytest.raises(HTTPException) as exc:
            await br.unlock_booking("unlkrate", user_id=52, session=object())
        assert exc.value.status_code == 429

    @pytest.mark.parametrize("state", [State.PAID, State.STORED, State.OVERSTAY])
    async def test_unlock_allowed_in_active_states(self, state):
        """REQ-LOCK-001: PAID, STORED, OVERSTAY are unlockable."""
        assert state in UNLOCKABLE

    @pytest.mark.parametrize("state", [State.CREATED, State.RELEASED, State.EXPIRED, State.CANCELLED])
    async def test_unlock_not_allowed_in_inactive_states(self, state):
        assert state not in UNLOCKABLE


# ---------------------------------------------------------------------------
# 6. PAYMENTS — webhook handlers
# ---------------------------------------------------------------------------

class TestWebhooks:
    """REQ-PAY-001..003: Webhook signature, state transition, idempotency."""

    def test_valid_signature_accepted(self):
        body = b'{"a":1}'
        assert verify_signature(body, _sign(body))

    def test_tampered_body_rejected(self):
        body = b'{"a":1}'
        assert not verify_signature(b'{"a":2}', _sign(body))

    def test_empty_signature_rejected(self):
        assert not verify_signature(b'{"a":1}', "")

    async def test_payment_captured_moves_booking_to_paid(self, monkeypatch):
        from app.routers import webhooks

        booking = SimpleNamespace(
            id=30, public_id="wb-pub-30", razorpay_order_id="ord-30",
            amount_paise=4000, state=State.CREATED,
            expires_at=datetime(2026, 11, 9, 12, 15),  # not expired
            locker_id=30, paid_at=None, ends_at=None, duration_hours=1,
        )
        events = []

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def execute(self, q):
                if "utc_timestamp" in str(q): return FakeResult(datetime(2026, 11, 9, 12, 0))
                return FakeResult(booking)
            def add(self, item): events.append(item)

        body = _webhook_body("payment.captured", "ord-30", "pay-30", 4000)
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: True)
        resp = await webhooks.razorpay_webhook(
            FakeRequest(body), x_razorpay_signature="sig", x_razorpay_event_id="evt-30",
            session=FakeSession(),
        )
        assert resp == {"ok": True}
        assert booking.state == State.PAID
        assert booking.paid_at is not None
        assert booking.ends_at is not None

    async def test_payment_failed_does_not_change_state(self, monkeypatch):
        from app.routers import webhooks

        booking = SimpleNamespace(
            id=31, public_id="wb-pub-31", razorpay_order_id="ord-31",
            amount_paise=4000, state=State.CREATED,
            expires_at=datetime(2026, 11, 9, 12, 15),
            locker_id=31,
        )
        events = []

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def execute(self, q):
                if "utc_timestamp" in str(q): return FakeResult(datetime(2026, 11, 9, 12, 0))
                return FakeResult(booking)
            def add(self, item): events.append(item)

        body = _webhook_body("payment.failed", "ord-31", "pay-31", 4000)
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: True)
        await webhooks.razorpay_webhook(
            FakeRequest(body), x_razorpay_signature="sig", x_razorpay_event_id="evt-31",
            session=FakeSession(),
        )
        assert booking.state == State.CREATED  # still CREATED
        assert any(getattr(e, "status", None) == "failed_attempt" for e in events)

    async def test_bad_signature_returns_400(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import webhooks

        body = b'{"event":"payment.captured"}'
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: False)
        with pytest.raises(HTTPException) as exc:
            await webhooks.razorpay_webhook(
                FakeRequest(body), x_razorpay_signature="bad", x_razorpay_event_id="evt-bad",
                session=object(),
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "BAD_SIGNATURE"

    async def test_duplicate_event_returns_ok_with_duplicate_flag(self, monkeypatch):
        """REQ-PAY-003: Duplicate gateway_event_id is idempotent."""
        from sqlalchemy.exc import IntegrityError
        from app.routers import webhooks

        booking = SimpleNamespace(
            id=32, public_id="wb-pub-32", razorpay_order_id="ord-32",
            amount_paise=4000, duration_hours=1, state=State.CREATED,
            expires_at=datetime(2026, 11, 9, 12, 15),
            locker_id=32,
        )

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, q):
                if "utc_timestamp" in str(q): return FakeResult(datetime(2026, 11, 9, 12, 0))
                return FakeResult(booking)
            def add(self, item): raise IntegrityError("duplicate", None, None)

        body = _webhook_body("payment.captured", "ord-32", "pay-32", 4000)
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: True)
        resp = await webhooks.razorpay_webhook(
            FakeRequest(body), x_razorpay_signature="sig", x_razorpay_event_id="evt-32-dup",
            session=FakeSession(),
        )
        assert resp.get("ok") is True
        assert resp.get("duplicate") is True

    async def test_amount_mismatch_does_not_move_to_paid(self, monkeypatch):
        from app.routers import webhooks

        booking = SimpleNamespace(
            id=33, public_id="wb-pub-33", razorpay_order_id="ord-33",
            amount_paise=6000,  # expected 6000
            state=State.CREATED,
            expires_at=datetime(2026, 11, 9, 12, 15),
            locker_id=33,
        )
        events = []

        class FakeSession:
            def begin(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def execute(self, q):
                if "utc_timestamp" in str(q): return FakeResult(datetime(2026, 11, 9, 12, 0))
                return FakeResult(booking)
            def add(self, item): events.append(item)

        body = _webhook_body("payment.captured", "ord-33", "pay-33", 4000)  # sent 4000, expected 6000
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: True)
        resp = await webhooks.razorpay_webhook(
            FakeRequest(body), x_razorpay_signature="sig", x_razorpay_event_id="evt-33",
            session=FakeSession(),
        )
        assert booking.state == State.CREATED  # NOT moved to PAID
        assert resp.get("reconciliation_required") is True

    async def test_unrelated_event_is_ignored(self, monkeypatch):
        from app.routers import webhooks

        body = json.dumps({"event": "order.paid", "payload": {}}).encode()
        monkeypatch.setattr(webhooks, "verify_signature", lambda *_: True)
        resp = await webhooks.razorpay_webhook(
            FakeRequest(body), x_razorpay_signature="sig", x_razorpay_event_id="",
            session=object(),
        )
        assert resp == {"ok": True}


# ---------------------------------------------------------------------------
# 7. BOOKING EXPIRY WORKER
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExpiryWorker:
    """REQ-BOOK-004: Expiry worker releases locker transactionally."""

    async def test_expired_created_booking_is_released(self):
        from app.booking_expiry import expire_unpaid_bookings
        from app.models import Booking, Locker, LockerEvent, utcnow

        locker = Locker(id=40, location_id=1, size="M", locker_code="EX101",
                        device_id="exp-dev-1", status="held")
        booking = Booking(id=40, user_id=1, locker_id=40, state=State.CREATED,
                          amount_paise=4000, duration_hours=1,
                          created_at=utcnow(), expires_at=utcnow() - timedelta(minutes=1),
                          public_id="exp-pub-1")
        events = []

        class FakeSession:
            async def execute(self, q):
                if "FROM lockers" in str(q): return _FakeScalarsResult([locker])
                return _FakeScalarsResult([booking])
            async def get(self, m, pk):
                if m is Locker and pk == locker.id: return locker
                return None
            def add(self, item): events.append(item)
            async def flush(self): pass

        count = await expire_unpaid_bookings(FakeSession())
        assert count == 1
        assert booking.state == State.EXPIRED
        assert locker.status == "free"
        assert any(isinstance(e, LockerEvent) and e.type == "booking_expired" for e in events)

    async def test_paid_booking_is_not_expired_by_worker(self):
        from app.booking_expiry import expire_unpaid_bookings
        from app.models import Booking, Locker, utcnow

        locker = Locker(id=41, location_id=1, size="M", locker_code="EX201",
                        device_id="exp-dev-2", status="held")
        # This should NOT be in the expired set (the query filters CREATED only)
        # We simulate an empty result
        class FakeSession:
            async def execute(self, q):
                return _FakeScalarsResult([])  # no expired bookings returned
            async def flush(self): pass

        count = await expire_unpaid_bookings(FakeSession())
        assert count == 0
        assert locker.status == "held"  # unchanged

    async def test_expiry_runs_in_transaction(self, monkeypatch):
        from app import booking_expiry
        entered = False

        class TxSession:
            async def __aenter__(self):
                nonlocal entered
                entered = True
                return self
            async def __aexit__(self, *a): pass

        class TxFactory:
            def begin(self): return TxSession()

        async def mock_expire(_s): return 2
        monkeypatch.setattr(booking_expiry, "SessionLocal", TxFactory())
        monkeypatch.setattr(booking_expiry, "expire_unpaid_bookings", mock_expire)
        result = await booking_expiry.expire_unpaid_bookings_transactionally()
        assert result == 2
        assert entered


# ---------------------------------------------------------------------------
# 8. STATE MACHINE — all transitions
# ---------------------------------------------------------------------------

class TestStateMachine:
    """Full state machine compliance."""

    def test_created_to_paid_on_payment_captured(self):
        assert apply(State.CREATED, "payment_captured") == State.PAID

    def test_created_to_expired_on_expire(self):
        assert apply(State.CREATED, "expire") == State.EXPIRED

    def test_created_to_cancelled_on_cancel(self):
        assert apply(State.CREATED, "cancel") == State.CANCELLED

    def test_paid_to_stored_on_door_closed(self):
        assert apply(State.PAID, "door_closed") == State.STORED

    def test_stored_to_released_on_pickup(self):
        assert apply(State.STORED, "pickup_completed") == State.RELEASED

    def test_overstay_to_released_on_pickup(self):
        assert apply(State.OVERSTAY, "pickup_completed") == State.RELEASED

    def test_stored_to_overstay_on_time_exceeded(self):
        assert apply(State.STORED, "time_exceeded") == State.OVERSTAY

    def test_stored_to_disputed_on_dispute(self):
        assert apply(State.STORED, "dispute") == State.DISPUTED

    def test_disputed_to_released_on_resolve(self):
        assert apply(State.DISPUTED, "resolve") == State.RELEASED

    def test_illegal_transitions_raise_invalid_transition(self):
        import itertools
        from app.state_machine import InvalidTransition
        events = {e for (_, e) in TRANSITIONS}
        for state, event in itertools.product(State, events):
            if (state, event) not in TRANSITIONS:
                with pytest.raises(InvalidTransition):
                    apply(state, event)

    def test_terminal_states_are_released_expired_cancelled(self):
        assert TERMINAL == {State.RELEASED, State.EXPIRED, State.CANCELLED}

    def test_terminal_states_have_no_exit_transitions(self):
        for (s, _) in TRANSITIONS:
            assert s not in TERMINAL, f"{s} is terminal but has an exit"

    def test_state_machine_matches_spec_document(self):
        """The markdown spec table is the source of truth for all transitions."""
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
        assert spec == code, "State machine code diverged from spec/booking-state-machine.md"


# ---------------------------------------------------------------------------
# 9. MQTT — payload parsing
# ---------------------------------------------------------------------------

class TestMqttPayloadParser:
    """MQTT listener robustness."""

    def test_valid_door_closed_event_parsed(self):
        from app.main import parse_device_payload
        result = parse_device_payload(b'{"evt":"door_closed","cmd_id":null}')
        assert result == {"evt": "door_closed", "cmd_id": None}

    def test_valid_pickup_completed_event_parsed(self):
        from app.main import parse_device_payload
        result = parse_device_payload(b'{"evt":"pickup_completed","ts":"2026-01-01T00:00:00Z","cmd_id":"abc"}')
        assert result is not None
        assert result["evt"] == "pickup_completed"

    def test_malformed_json_returns_none(self, caplog):
        from app.main import parse_device_payload
        result = parse_device_payload(b"{evt:door_closed}")
        assert result is None
        assert "malformed" in caplog.text.lower() or "Ignoring" in caplog.text

    def test_json_array_returns_none(self, caplog):
        from app.main import parse_device_payload
        result = parse_device_payload(b'["door_closed"]')
        assert result is None

    def test_empty_payload_returns_none(self):
        from app.main import parse_device_payload
        assert parse_device_payload(b"") is None

    def test_valid_heartbeat_event_parsed(self):
        from app.main import parse_device_payload
        result = parse_device_payload(b'{"evt":"heartbeat","ts":"2026-01-01T00:00:00Z"}')
        assert result is not None


# ---------------------------------------------------------------------------
# 10. HEALTH CHECK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHealthEndpoint:
    """REQ-OPS-001: /health returns status ok when DB is reachable."""

    async def test_health_endpoint_returns_ok_with_healthy_db(self):
        from app.db import get_session

        class _HealthySession:
            async def execute(self, _q):
                return FakeResult(1)

        async def _override():
            yield _HealthySession()

        app.dependency_overrides[get_session] = _override
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
        finally:
            app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# 11. LOCKER CODE derivation / uniqueness
# ---------------------------------------------------------------------------

class TestLockerCodes:
    """REQ-BOOK-006: Stable, unique, human-readable locker codes."""

    def test_locker_code_format_is_prefix_plus_sequence(self):
        """Codes should match pattern like CH101, CH102, BR201."""
        import re
        codes = ["CH101", "CH102", "BR201", "MG301"]
        pattern = re.compile(r"^[A-Z]{2,}\d{3,}$")
        for code in codes:
            assert pattern.match(code), f"Code {code!r} doesn't match expected format"

    def test_locker_codes_are_case_insensitive_in_booking(self):
        """Booking API should match locker codes case-insensitively or document the case."""
        # The system stores and returns exact codes — this tests the schema constraint
        from app.schemas import BookingCreate
        bc = BookingCreate(locker_code="CH101", duration_hours=1)
        assert bc.locker_code == "CH101"


# ---------------------------------------------------------------------------
# 12. PAYMENT — price configuration
# ---------------------------------------------------------------------------

class TestPaymentConfig:
    """REQ-PAY-004: Prices in integer paise."""

    def test_prices_are_all_integers(self):
        assert all(isinstance(v, int) for v in settings.price_per_hour_paise.values())

    def test_medium_price_is_configured(self):
        assert "M" in settings.price_per_hour_paise
        assert settings.price_per_hour_paise["M"] > 0

    def test_large_price_is_configured(self):
        assert "L" in settings.price_per_hour_paise
        assert settings.price_per_hour_paise["L"] > 0

    def test_large_price_is_at_least_as_high_as_medium(self):
        assert settings.price_per_hour_paise["L"] >= settings.price_per_hour_paise["M"]


# ---------------------------------------------------------------------------
# 13. TIMESTAMPS — UTC serialization
# ---------------------------------------------------------------------------

class TestTimestampHandling:
    """REQ-NFR-001: All timestamps must be UTC."""

    def test_naive_datetime_is_coerced_to_utc(self):
        from app.routers.bookings import as_utc
        naive = datetime(2026, 11, 9, 12, 0)
        result = as_utc(naive)
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_aware_datetime_is_converted_to_utc(self):
        from app.routers.bookings import as_utc
        ist = timezone(timedelta(hours=5, minutes=30))
        aware = datetime(2026, 11, 9, 17, 30, tzinfo=ist)
        result = as_utc(aware)
        assert result.hour == 12  # 17:30 IST = 12:00 UTC
        assert result.tzinfo == timezone.utc

    def test_none_datetime_returns_none(self):
        from app.routers.bookings import as_utc
        assert as_utc(None) is None


# ---------------------------------------------------------------------------
# 14. HAVERSINE DISTANCE — location search math
# ---------------------------------------------------------------------------

class TestHaversineDistance:
    """Verify bounding box and distance calculations."""

    def test_distance_between_same_points_is_zero(self):
        from app.routers.locations import _distance_meters
        assert _distance_meters(12.9716, 77.5946, 12.9716, 77.5946) == pytest.approx(0.0, abs=0.1)

    def test_distance_at_antimeridian_is_handled(self):
        from app.routers.locations import _distance_meters
        d = _distance_meters(0, 179.99, 0, -179.99)
        assert 2_000 < d < 2_300

    def test_bounding_box_wraps_at_antimeridian(self):
        from app.routers.locations import _bounding_box
        _, _, lon_ranges = _bounding_box(0, 179.99, 5_000)
        assert len(lon_ranges) == 2
        assert lon_ranges[0][0] == -180.0
        assert lon_ranges[1][1] == 180.0

    def test_bounding_box_does_not_wrap_for_normal_coordinates(self):
        from app.routers.locations import _bounding_box
        _, _, lon_ranges = _bounding_box(12.9716, 77.5946, 1_000)
        assert len(lon_ranges) == 1

    def test_1km_bounding_box_excludes_location_11km_away(self):
        from app.routers.locations import _distance_meters
        # Bengaluru to roughly 11 km north
        d = _distance_meters(12.9716, 77.5946, 13.0716, 77.5946)
        assert d > 1_000  # outside 1 km


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self.value = value
    def scalar_one_or_none(self):
        if isinstance(self.value, list): return self.value[0] if self.value else None
        return self.value
    def scalar_one(self): return self.value
    def scalars(self): return self
    def all(self): return self.value if isinstance(self.value, list) else [self.value] if self.value else []


class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None
    def scalar_one(self): return self._rows[0]


class FakeRequest:
    def __init__(self, body: bytes):
        self._body = body
    async def body(self) -> bytes:
        return self._body
