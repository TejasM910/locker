"""Auth API endpoints using Dependency Injection (DIP)."""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import rate_limited
from app.db import get_session
from app.models import User
from ..domain.phone import normalize_phone
from ..ports.contracts import OtpServicePort, SmsGatewayPort, TokenServicePort
from ..adapters.services import InMemoryOtpService, InMemoryTokenService, MockSmsGateway

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Default singleton instances (can be overridden via dependency injection)
_otp_service = InMemoryOtpService()
_token_service = InMemoryTokenService()
_sms_gateway = MockSmsGateway()


def get_otp_service() -> OtpServicePort:
    return _otp_service


def get_token_service() -> TokenServicePort:
    return _token_service


def get_sms_gateway() -> SmsGatewayPort:
    return _sms_gateway


class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    otp: str


@router.post("/otp/request")
async def otp_request(
    body: OtpRequest,
    session: AsyncSession = Depends(get_session),
    otp_service: OtpServicePort = Depends(get_otp_service),
    sms_gateway: SmsGatewayPort = Depends(get_sms_gateway),
):
    phone = normalize_phone(body.phone)
    if rate_limited(phone):
        raise HTTPException(429, "OTP_RATE_LIMITED")

    code = otp_service.issue_otp(phone)
    await sms_gateway.send_otp(phone, code)

    # Maintain backward compatibility with dev contracts / tests
    return {"status": "sent", "phone": phone, "debug_code": code}


@router.post("/otp/verify")
async def otp_verify(
    body: OtpVerify,
    session: AsyncSession = Depends(get_session),
    otp_service: OtpServicePort = Depends(get_otp_service),
    token_service: TokenServicePort = Depends(get_token_service),
):
    phone = normalize_phone(body.phone)
    if not otp_service.verify_otp(phone, body.otp):
        raise HTTPException(400, "INVALID_OTP")

    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is None:
        user = User(phone=phone)
        session.add(user)
        await session.flush()
    await session.commit()

    token = token_service.create_token(int(user.id))
    return {"token": token, "user_id": int(user.id)}


async def get_user_id_from_auth_header(
    authorization: str | None = Header(None),
    token_service: TokenServicePort = Depends(get_token_service),
) -> int | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token_service.resolve_token(token)


async def current_user_id(
    authorization: str | None = Header(None),
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    token_service: TokenServicePort = Depends(get_token_service),
) -> int:
    resolved_id = await get_user_id_from_auth_header(authorization, token_service)
    if resolved_id is not None:
        return resolved_id
    if x_user_id is not None:
        return x_user_id
    raise HTTPException(401, "UNAUTHORIZED")
