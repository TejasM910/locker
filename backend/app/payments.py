import hashlib
import hmac

import httpx

from .config import settings


async def create_order(amount_paise: int, receipt: str) -> str:
    if not settings.razorpay_key_id:  # dev mode without keys
        return f"order_dev_{receipt}"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://api.razorpay.com/v1/orders",
                         auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                         json={"amount": amount_paise, "currency": "INR", "receipt": receipt})
        r.raise_for_status()
        return r.json()["id"]


def verify_signature(body: bytes, signature: str) -> bool:  # REQ-PAY-002
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
