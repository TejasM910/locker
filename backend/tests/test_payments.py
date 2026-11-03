import hashlib
import hmac
import pytest

from app.config import settings
from app.payments import verify_signature


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
