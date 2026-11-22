"""SQLAlchemy transactional adapter for BookingRepositoryPort."""

import secrets
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, Locker, State
from ..ports.contracts import BookingRepositoryPort


class SqlAlchemyBookingRepository(BookingRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_public_id(self, public_id: str) -> Optional[Any]:
        query = select(Booking).where(Booking.public_id == public_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def create_booking_hold(
        self,
        user_id: int,
        locker_code: str,
        duration_hours: int,
        amount_paise: int,
        expires_at: datetime,
    ) -> Any:
        # Atomic lock
        locker_query = (
            select(Locker)
            .where(Locker.locker_code == locker_code, Locker.status == "free")
            .with_for_update(skip_locked=True)
        )
        locker = (await self.session.execute(locker_query)).scalar_one_or_none()
        if not locker:
            return None

        locker.status = "held"
        public_id = secrets.token_hex(16)
        booking = Booking(
            public_id=public_id,
            user_id=user_id,
            locker_id=locker.id,
            state=State.CREATED,
            amount_paise=amount_paise,
            duration_hours=duration_hours,
            expires_at=expires_at,
        )
        self.session.add(booking)
        await self.session.flush()
        return booking, locker

    async def cancel_booking(self, booking_id: int) -> bool:
        booking = (await self.session.execute(select(Booking).where(Booking.id == booking_id))).scalar_one_or_none()
        if not booking or booking.state != State.CREATED:
            return False
        booking.state = State.CANCELLED
        locker = (await self.session.execute(select(Locker).where(Locker.id == booking.locker_id))).scalar_one_or_none()
        if locker:
            locker.status = "free"
        await self.session.flush()
        return True
