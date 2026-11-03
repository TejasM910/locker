import pytest
from types import SimpleNamespace

from app.auth import (
    MAX_UNLOCK_REQUESTS_PER_WINDOW,
    UNLOCK_RATE_LIMIT_SECONDS,
    create_token,
    issue_otp,
    rate_limited,
    resolve_token,
    verify_otp,
)


def test_otp_round_trip_works_and_is_single_use():
    phone = "9999999999"
    code = issue_otp(phone)

    assert code.isdigit()
    assert len(code) == 6
    assert verify_otp(phone, code) is True
    assert verify_otp(phone, code) is False


def test_otp_requests_are_rate_limited():
    phone = "8888888888"
    for _ in range(5):
        assert not rate_limited(phone)
    assert rate_limited(phone) is True


def test_unlock_requests_are_rate_limited_per_user():
    user_key = "unlock:test-user-42"
    for _ in range(MAX_UNLOCK_REQUESTS_PER_WINDOW):
        assert not rate_limited(user_key, MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS)
    assert rate_limited(user_key, MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS) is True


def test_bearer_tokens_resolve_to_user_id():
    user_id = 42
    token = create_token(user_id)
    assert resolve_token(token) == user_id


@pytest.mark.asyncio
async def test_otp_verification_commits_new_user(monkeypatch):
    from app.routers import auth as auth_router

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        async def execute(self, _query):
            return FakeResult()

        def add(self, user):
            self.added.append(user)

        async def flush(self):
            self.added[0].id = 17

        async def commit(self):
            self.committed = True

    monkeypatch.setattr(auth_router, "verify_otp", lambda _phone, _otp: True)
    monkeypatch.setattr(auth_router, "create_token", lambda _user_id: "test-token")
    session = FakeSession()

    response = await auth_router.otp_verify(
        auth_router.OtpVerify(phone="9999999999", otp="123456"),
        session,
    )

    assert session.committed
    assert session.added[0].phone == "9999999999"
    assert response == {"token": "test-token", "user_id": 17}


@pytest.mark.asyncio
async def test_unlock_commits_audit_event_after_owned_booking_query(monkeypatch):
    from app.routers import bookings as booking_router
    from app.state_machine import State

    booking = SimpleNamespace(
        id=12,
        public_id="booking-public-id",
        locker_id=3,
        state=State.PAID,
        locker=SimpleNamespace(device_id="locker-3"),
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, event):
            self.added.append(event)

        async def commit(self):
            self.committed = True

    async def owned(_session, _public_id, _user_id):
        return booking

    async def publish_unlock(_device_id, _public_id):
        return "cmd-123"

    monkeypatch.setattr(booking_router, "_owned", owned)
    monkeypatch.setattr(booking_router, "rate_limited", lambda *_args: False)
    monkeypatch.setattr(booking_router.mqtt, "publish_unlock", publish_unlock)
    session = FakeSession()

    response = await booking_router.unlock_booking("booking-public-id", 8, session)

    assert response == {"cmd_id": "cmd-123"}
    assert session.committed
    assert len(session.added) == 1
    assert session.added[0].type == "unlock_requested"


def test_mqtt_payload_parser_ignores_invalid_json(caplog):
    from app.main import parse_device_payload

    assert parse_device_payload(b'{"evt":"door_closed","cmd_id":null}') == {
        "evt": "door_closed",
        "cmd_id": None,
    }
    assert parse_device_payload(b"{evt:door_closed}") is None
    assert parse_device_payload(b"[]") is None
    assert "Ignoring malformed MQTT JSON payload" in caplog.text
