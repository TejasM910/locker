"""Background worker for REQ-BOOK-004: expire unpaid bookings and free their lockers."""

import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionLocal
from .models import Booking, Locker, LockerEvent, utcnow
from .state_machine import InvalidTransition, State, apply

log = logging.getLogger("booking_expiry")


async def expire_unpaid_bookings(session: AsyncSession) -> int:
    """Set CREATED bookings whose hold expired to EXPIRED and release the locker."""
    now = utcnow()
    rows = (
        await session.execute(
            select(Booking)
            .where(Booking.state == State.CREATED, Booking.expires_at <= now)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    count = 0
    for booking in rows:
        locker = await session.get(Locker, booking.locker_id)
        if locker is None:
            continue
        try:
            booking.state = apply(booking.state, "expire")
        except InvalidTransition:
            continue

        locker.status = "free"
        session.add(
            LockerEvent(
                locker_id=locker.id,
                booking_id=booking.id,
                type="booking_expired",
                payload={
                    "public_id": booking.public_id,
                    "expired_at": now.isoformat(),
                },
            )
        )
        count += 1

    await session.flush()
    return count


async def expire_unpaid_bookings_transactionally() -> int:
    async with SessionLocal.begin() as session:
        return await expire_unpaid_bookings(session)


async def run_booking_expiry_loop(interval_seconds: int = 30) -> None:
    while True:
        try:
            await expire_unpaid_bookings_transactionally()
        except Exception:
            log.exception("booking expiry loop failed")
        await asyncio.sleep(interval_seconds)
