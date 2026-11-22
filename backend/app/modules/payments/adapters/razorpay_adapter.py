"""Razorpay implementation of PaymentGatewayPort."""

import hmac
import hashlib
import json
import logging
from typing import Any, Dict

from app.config import settings
from ..ports.contracts import PaymentGatewayPort

logger = logging.getLogger(__name__)


class RazorpayGatewayAdapter(PaymentGatewayPort):
    def __init__(self, key_id: str = "", key_secret: str = "", webhook_secret: str = ""):
        self.key_id = key_id or settings.razorpay_key_id
        self.key_secret = key_secret or settings.razorpay_key_secret
        self.webhook_secret = webhook_secret or settings.razorpay_webhook_secret

    def create_order(self, amount_paise: int, receipt: str, notes: Dict[str, Any]) -> str:
        if not (self.key_id and self.key_secret):
            # Development fallback order id
            import secrets
            return f"order_dev_{secrets.token_hex(8)}"
        import razorpay
        client = razorpay.Client(auth=(self.key_id, self.key_secret))
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes,
        })
        return order["id"]

    def verify_webhook_signature(self, body_bytes: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
