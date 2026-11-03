from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import mqtt, payments
from ..auth import MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS, rate_limited
from ..config import settings
from ..db import get_session
from ..models import Booking, Locker, LockerEvent, User, utcnow
from ..routers.auth import get_user_id_from_auth_header
from ..schemas import BookingCreate, BookingOut
from ..state_machine import UNLOCKABLE

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])


async def current_user_id(
    x_user_id: int | None = Header(default=None, alias="X-User-Id"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> int:
    """Supports dev header auth and bearer tokens returned by the OTP flow."""
    if authorization is not None:
        token_user_id = await get_user_id_from_auth_header(authorization)
        if token_user_id is not None:
            return token_user_id
    if x_user_id is not None:
        return x_user_id
    raise HTTPException(401, "AUTH_REQUIRED")


def to_out(b: Booking) -> BookingOut:
    return BookingOut(public_id=b.public_id, state=b.state.value, amount_paise=b.amount_paise,
                      duration_hours=b.duration_hours, razorpay_order_id=b.razorpay_order_id,
                      expires_at=b.expires_at)


@router.post("", status_code=201, response_model=BookingOut)
async def create_booking(body: BookingCreate, user_id: int = Depends(current_user_id),
                         session: AsyncSession = Depends(get_session)):
    price = settings.price_per_hour_paise[body.size]
    async with session.begin():
        if await session.get(User, user_id) is None:
            raise HTTPException(404, "USER_NOT_FOUND")
        # ADR-2 / REQ-BOOK-002: lock one free locker; concurrent requests skip it
        locker = (await session.execute(
            select(Locker).where(Locker.location_id == body.location_id, Locker.size == body.size,
                                 Locker.status == "free")
            .limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if locker is None:  # REQ-BOOK-003
            raise HTTPException(409, "NO_LOCKER_AVAILABLE")
        locker.status = "held"
        booking = Booking(user_id=user_id, locker_id=locker.id, amount_paise=price * body.duration_hours,
                          duration_hours=body.duration_hours,
                          expires_at=utcnow() + timedelta(minutes=settings.booking_hold_minutes))
        session.add(booking)
        await session.flush()
        booking.razorpay_order_id = await payments.create_order(booking.amount_paise, booking.public_id)
    return to_out(booking)


async def _owned(session: AsyncSession, public_id: str, user_id: int) -> Booking:
    b = (await session.execute(select(Booking).where(Booking.public_id == public_id))).scalar_one_or_none()
    if b is None:
        raise HTTPException(404, "NOT_FOUND")
    if b.user_id != user_id:
        raise HTTPException(403, "FORBIDDEN")
    return b


@router.get("/{public_id}", response_model=BookingOut)
async def get_booking(public_id: str, user_id: int = Depends(current_user_id),
                      session: AsyncSession = Depends(get_session)):
    return to_out(await _owned(session, public_id, user_id))


@router.post("/{public_id}/unlock", status_code=202)
async def unlock_booking(public_id: str, user_id: int = Depends(current_user_id),
                         session: AsyncSession = Depends(get_session)):
    b = await _owned(session, public_id, user_id)  # REQ-LOCK-002 (owner only)
    if rate_limited(f"unlock:{user_id}", MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS):
        raise HTTPException(429, "UNLOCK_RATE_LIMITED")
    if b.state not in UNLOCKABLE:  # REQ-LOCK-002 (state)
        raise HTTPException(403, f"NOT_UNLOCKABLE_IN_{b.state.value}")
    cmd_id = await mqtt.publish_unlock(b.locker.device_id, b.public_id)  # REQ-SEC-001
    session.add(LockerEvent(locker_id=b.locker_id, booking_id=b.id, type="unlock_requested",
                            payload={"cmd_id": cmd_id, "user_id": user_id}))  # REQ-LOCK-006
    await session.commit()
    return {"cmd_id": cmd_id}
