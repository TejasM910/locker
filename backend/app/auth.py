import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException

OTP_TTL_SECONDS = 300
MAX_OTP_REQUESTS_PER_WINDOW = 5
OTP_RATE_LIMIT_SECONDS = 600
MAX_UNLOCK_REQUESTS_PER_WINDOW = 30
UNLOCK_RATE_LIMIT_SECONDS = 60
TOKEN_TTL_SECONDS = 86400

_otp_store: dict[str, dict] = {}
_request_history: defaultdict[str, list[datetime]] = defaultdict(list)
_active_tokens: dict[str, dict] = {}
_store_lock = Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        raise HTTPException(400, "INVALID_PHONE")
    return digits


def generate_otp() -> str:
    return f"{random.randint(100000, 999999):06d}"


def rate_limited(
    key: str,
    max_requests: int = MAX_OTP_REQUESTS_PER_WINDOW,
    window_seconds: int = OTP_RATE_LIMIT_SECONDS,
) -> bool:
    with _store_lock:
        now = utcnow()
        history = [ts for ts in _request_history[key] if ts > now - timedelta(seconds=window_seconds)]
        _request_history[key] = history
        if len(history) >= max_requests:
            return True
        history.append(now)
        return False


def issue_otp(phone: str) -> str:
    normalized = normalize_phone(phone)
    with _store_lock:
        code = generate_otp()
        _otp_store[normalized] = {
            "code": code,
            "expires_at": utcnow() + timedelta(seconds=OTP_TTL_SECONDS),
        }
        return code


def verify_otp(phone: str, code: str) -> bool:
    normalized = normalize_phone(phone)
    with _store_lock:
        entry = _otp_store.get(normalized)
        if not entry:
            return False
        if entry["expires_at"] < utcnow():
            _otp_store.pop(normalized, None)
            return False
        if str(code) != str(entry["code"]):
            return False
        _otp_store.pop(normalized, None)
        return True


def create_token(user_id: int) -> str:
    token = f"lock_{random.getrandbits(128):032x}"
    with _store_lock:
        _active_tokens[token] = {
            "user_id": user_id,
            "expires_at": utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS),
        }
    return token


def resolve_token(token: str) -> int | None:
    if not token:
        return None
    with _store_lock:
        entry = _active_tokens.get(token)
        if not entry:
            return None
        if entry["expires_at"] < utcnow():
            _active_tokens.pop(token, None)
            return None
        return int(entry["user_id"])


def clear_expired_tokens() -> None:
    with _store_lock:
        now = utcnow()
        for token, entry in list(_active_tokens.items()):
            if entry["expires_at"] < now:
                del _active_tokens[token]
