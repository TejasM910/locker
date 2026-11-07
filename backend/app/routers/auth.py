from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_token, issue_otp, normalize_phone, rate_limited, resolve_token, verify_otp
from ..db import get_session
from ..models import User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    otp: str


@router.post("/otp/request")
async def otp_request(body: OtpRequest, session: AsyncSession = Depends(get_session)):
    phone = normalize_phone(body.phone)
    if rate_limited(phone):
        raise HTTPException(429, "OTP_RATE_LIMITED")
    code = issue_otp(phone)
    # Dev-only flow: return the generated code so it can be used in tests and local demo.
    return {"status": "sent", "phone": phone, "debug_code": code}


@router.post("/otp/verify")
async def otp_verify(body: OtpVerify, session: AsyncSession = Depends(get_session)):
    phone = normalize_phone(body.phone)
    if not verify_otp(phone, body.otp):
        raise HTTPException(400, "INVALID_OTP")
    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is None:
        user = User(phone=phone)
        session.add(user)
        await session.flush()
    await session.commit()
    token = create_token(int(user.id))
    return {"token": token, "user_id": int(user.id)}


async def get_user_id_from_auth_header(authorization: str | None) -> int | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return resolve_token(token)


async def current_user_id(
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> int:
    """Resolve the OTP bearer session, with the existing local development fallback."""
    if authorization is not None:
        token_user_id = await get_user_id_from_auth_header(authorization)
        if token_user_id is not None:
            return token_user_id
    if x_user_id is not None:
        return x_user_id
    raise HTTPException(401, "AUTH_REQUIRED")
