import hashlib
import hmac
import json
from datetime import datetime
from types import SimpleNamespace
import pytest

from app.config import settings
from app.payments import verify_signature
from app.routers import webhooks
from app.state_machine import State


def sign(body: bytes) -> str:
    return hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.req("REQ-PAY-002")
def test_valid_signature_accepted():
    assert verify_signature(b'{"a":1}', sign(b'{"a":1}'))


@pytest.mark.req("REQ-PAY-002")
def test_tampered_or_missing_signature_rejected():
    assert not verify_signature(b'{"a":2}', sign(b'{"a":1}'))
    assert not verify_signature(b'{"a":1}', "")


@pytest.mark.req("REQ-PAY-004")
def test_prices_are_integer_paise():
    assert all(isinstance(v, int) for v in settings.price_per_hour_paise.values())


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class FakeWebhookSession:
    def __init__(self, booking):
        self.booking = booking
        self.added = []

    def begin(self):
        return FakeTransaction()

    async def execute(self, query):
        if "utc_timestamp" in str(query):
            return FakeResult(datetime(2026, 11, 9, 12, 0))
        return FakeResult(self.booking)

    def add(self, item):
        self.added.append(item)


@pytest.mark.asyncio
async def test_failed_payment_attempt_is_recorded_without_releasing_retryable_hold(monkeypatch):
    booking = SimpleNamespace(
        id=1,
        public_id="booking-1",
        razorpay_order_id="order-1",
        amount_paise=4000,
        state=State.CREATED,
        expires_at=datetime(2026, 11, 9, 12, 15),
        locker_id=1,
    )
    session = FakeWebhookSession(booking)

    class Request:
        async def body(self):
            return json.dumps(
                {
                    "event": "payment.failed",
                    "payload": {
                        "payment": {
                            "entity": {
                                "order_id": "order-1",
                                "id": "payment-1",
                                "amount": 4000,
                                "currency": "INR",
                            }
                        }
                    },
                }
            ).encode()

    monkeypatch.setattr(webhooks, "verify_signature", lambda *_args: True)
    response = await webhooks.razorpay_webhook(
        Request(),
        x_razorpay_signature="valid",
        x_razorpay_event_id="event-1",
        session=session,
    )

    assert response == {"ok": True}
    assert booking.state == State.CREATED
    assert len(session.added) == 1
    assert session.added[0].status == "failed_attempt"
