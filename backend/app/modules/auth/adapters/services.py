"""Infrastructure adapters for Auth."""

import logging
import random
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from ..ports.contracts import OtpServicePort, SmsGatewayPort, TokenServicePort

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = 86400


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MockSmsGateway(SmsGatewayPort):
    """Development / Testing SMS gateway that logs outgoing OTPs (LSP/OCP)."""

    async def send_otp(self, phone: str, otp_code: str) -> bool:
        logger.info("[MockSmsGateway] Dispatched OTP %s to phone %s", otp_code, phone)
        return True


class TwilioSmsGateway(SmsGatewayPort):
    """Twilio SMS Gateway implementation for real-world SMS delivery (LSP/OCP)."""

    def __init__(self, account_sid: str = "", auth_token: str = "", from_phone: str = ""):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_phone = from_phone

    async def send_otp(self, phone: str, otp_code: str) -> bool:
        if not (self.account_sid and self.auth_token and self.from_phone):
            logger.warning("[TwilioSmsGateway] Missing Twilio credentials. Falling back to log output.")
            logger.info("[SMS] Your Locker verification code is %s", otp_code)
            return True
        try:
            # Twilio integration hook
            import httpx
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    url,
                    data={
                        "To": f"+{phone}" if not phone.startswith("+") else phone,
                        "From": self.from_phone,
                        "Body": f"Your Locker verification code is {otp_code}. Valid for 5 minutes.",
                    },
                    auth=(self.account_sid, self.auth_token),
                )
                return res.status_code in (200, 201)
        except Exception as e:
            logger.error("[TwilioSmsGateway] Failed to dispatch SMS: %s", e)
            return False


class InMemoryOtpService(OtpServicePort):
    """In-memory thread-safe OTP store and generator."""

    def __init__(self):
        self._store: dict[str, dict] = {}
        self._lock = Lock()

    def issue_otp(self, phone: str) -> str:
        code = f"{random.randint(100000, 999999):06d}"
        expires_at = utcnow() + timedelta(seconds=OTP_TTL_SECONDS)
        with self._lock:
            self._store[phone] = {"code": code, "expires_at": expires_at}
        return code

    def verify_otp(self, phone: str, code: str) -> bool:
        with self._lock:
            entry = self._store.get(phone)
            if not entry:
                return False
            if utcnow() > entry["expires_at"]:
                self._store.pop(phone, None)
                return False
            if entry["code"] != code:
                return False
            self._store.pop(phone, None)
            return True


class InMemoryTokenService(TokenServicePort):
    """In-memory thread-safe session token store."""

    def __init__(self):
        self._tokens: dict[str, dict] = {}
        self._lock = Lock()

    def create_token(self, user_id: int) -> str:
        token = secrets.token_hex(32)
        expires_at = utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS)
        with self._lock:
            self._tokens[token] = {"user_id": user_id, "expires_at": expires_at}
        return token

    def resolve_token(self, token: str) -> Optional[int]:
        if not token:
            return None
        with self._lock:
            entry = self._tokens.get(token)
            if not entry:
                return None
            if utcnow() > entry["expires_at"]:
                self._tokens.pop(token, None)
                return None
            return entry["user_id"]
