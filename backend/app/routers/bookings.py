import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from .. import mqtt, payments
from ..auth import MAX_UNLOCK_REQUESTS_PER_WINDOW, UNLOCK_RATE_LIMIT_SECONDS, rate_limited
from ..config import settings
from ..db import get_session
from ..models import Booking, BookingRequest, Locker, LockerEvent, User, utcnow
from ..routers.auth import current_user_id
from ..schemas import BookingCreate, BookingOut
from ..state_machine import State, UNLOCKABLE, apply

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])
log = logging.getLogger("app.bookings")


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_out(b: Booking) -> BookingOut:
    return BookingOut(
        public_id=b.public_id,
        state=b.state.value,
        locker_code=b.locker.locker_code,
        size=b.locker.size,
        amount_paise=b.amount_paise,
        duration_hours=b.duration_hours,
        razorpay_order_id=b.razorpay_order_id,
        expires_at=as_utc(b.expires_at),
        ends_at=as_utc(b.ends_at),
    )


@router.post("", status_code=201, response_model=BookingOut)
async def create_booking(body: BookingCreate, user_id: int = Depends(current_user_id),
                         session: AsyncSession = Depends(get_session),
                         idempotency_key: str | None = Header(
                             default=None, alias="Idempotency-Key", min_length=8, max_length=128
                         )):
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    booking_id: int | None = None
    try:
        async with session.begin():
            if await session.get(User, user_id) is None:
                raise HTTPException(404, "USER_NOT_FOUND")
            if idempotency_key:
                prior = (
                    await session.execute(
                        select(BookingRequest).where(
                            BookingRequest.user_id == user_id,
                            BookingRequest.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if prior:
                    if prior.request_hash != request_hash:
                        raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                    booking = await session.get(Booking, prior.booking_id)
                    if booking is None:
                        raise HTTPException(409, "BOOKING_REQUEST_INCOMPLETE")
                    return to_out(booking)
                request_record = BookingRequest(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                session.add(request_record)
                await session.flush()

            active_booking = exists(
                select(Booking.id).where(
                    Booking.locker_id == Locker.id,
                    Booking.state.in_(
                        (State.CREATED, State.PAID, State.STORED, State.OVERSTAY, State.DISPUTED)
                    ),
                )
            )
            locker_query = select(Locker).where(Locker.status == "free", ~active_booking)
            if body.locker_code is not None:
                locker_query = locker_query.where(Locker.locker_code == body.locker_code)
            else:
                locker_query = locker_query.where(
                    Locker.location_id == body.location_id,
                    Locker.size == body.size,
                )
            locker = (
                await session.execute(
                    locker_query.limit(1).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if locker and (
                (body.location_id is not None and locker.location_id != body.location_id)
                or (body.size is not None and locker.size != body.size)
            ):
                locker = None
            if locker is None:
                raise HTTPException(409, "NO_LOCKER_AVAILABLE")

            price = settings.price_per_hour_paise[locker.size]
            locker.status = "held"
            db_now = (await session.execute(select(func.utc_timestamp(3)))).scalar_one()
            booking = Booking(
                user_id=user_id,
                locker_id=locker.id,
                amount_paise=price * body.duration_hours,
                duration_hours=body.duration_hours,
                expires_at=db_now + timedelta(minutes=settings.booking_hold_minutes),
            )
            session.add(booking)
            await session.flush()
            if idempotency_key:
                request_record.booking_id = booking.id
            booking_id = booking.id
    except HTTPException as error:
        if error.status_code != 409 or error.detail != "NO_LOCKER_AVAILABLE" or not idempotency_key:
            raise
        prior = (
            await session.execute(
                select(BookingRequest).where(
                    BookingRequest.user_id == user_id,
                    BookingRequest.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if prior is None:
            raise
        if prior.request_hash != request_hash:
            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
        booking = await session.get(Booking, prior.booking_id)
        if booking is None:
            raise HTTPException(409, "BOOKING_REQUEST_INCOMPLETE")
        return to_out(booking)
    except IntegrityError:
        if not idempotency_key:
            raise
        prior = (
            await session.execute(
                select(BookingRequest).where(
                    BookingRequest.user_id == user_id,
                    BookingRequest.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if prior is None:
            raise
        if prior.request_hash != request_hash:
            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
        booking = await session.get(Booking, prior.booking_id)
        if booking is None:
            raise HTTPException(409, "BOOKING_REQUEST_INCOMPLETE")
        return to_out(booking)

    try:
        order_id = await payments.create_order(booking.amount_paise, booking.public_id)
    except httpx.HTTPError as error:
        log.exception("Payment order creation failed for booking %s", booking.public_id)
        async with session.begin():
            booking = (
                await session.execute(
                    select(Booking).where(Booking.id == booking_id).with_for_update()
                )
            ).scalar_one()
            locker = (
                await session.execute(
                    select(Locker).where(Locker.id == booking.locker_id).with_for_update()
                )
            ).scalar_one()
            if booking.state == State.CREATED:
                booking.state = apply(booking.state, "cancel")
                if locker.status == "held":
                    locker.status = "free"
                session.add(
                    LockerEvent(
                        locker_id=locker.id,
                        booking_id=booking.id,
                        type="payment_order_failed",
                        payload={"public_id": booking.public_id},
                    )
                )
        raise HTTPException(502, "PAYMENT_ORDER_CREATION_FAILED") from error

    async with session.begin():
        booking = (
            await session.execute(
                select(Booking).where(Booking.id == booking_id).with_for_update()
            )
        ).scalar_one()
        booking.razorpay_order_id = order_id
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


@router.post("/{public_id}/cancel")
async def cancel_booking(public_id: str, user_id: int = Depends(current_user_id),
                         session: AsyncSession = Depends(get_session)):
    async with session.begin():
        booking = (
            await session.execute(
                select(Booking).where(Booking.public_id == public_id).with_for_update()
            )
        ).scalar_one_or_none()
        if booking is None:
            raise HTTPException(404, "NOT_FOUND")
        if booking.user_id != user_id:
            raise HTTPException(403, "FORBIDDEN")
        if booking.state == State.CANCELLED:
            return {"public_id": booking.public_id, "state": booking.state.value}
        if booking.state != State.CREATED:
            raise HTTPException(409, f"CANNOT_CANCEL_{booking.state.value}")

        locker = (
            await session.execute(
                select(Locker).where(Locker.id == booking.locker_id).with_for_update()
            )
        ).scalar_one_or_none()
        booking.state = apply(booking.state, "cancel")
        if locker and locker.status == "held":
            locker.status = "free"
        session.add(
            LockerEvent(
                locker_id=booking.locker_id,
                booking_id=booking.id,
                type="booking_cancelled",
                payload={"public_id": booking.public_id, "user_id": user_id},
            )
        )
    return {"public_id": booking.public_id, "state": booking.state.value}


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
