"""SQLAlchemy implementation of LocationRepositoryPort (LSP & DIP)."""

from typing import Any, List, Optional, Tuple
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, Location, Locker
from ..ports.contracts import LocationRepositoryPort

ACTIVE_BOOKING_STATES = ("CREATED", "PAID", "STORED", "OVERSTAY", "DISPUTED")


class SqlAlchemyLocationRepository(LocationRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_nearby_candidates(
        self,
        min_latitude: float,
        max_latitude: float,
        longitude_ranges: List[Tuple[float, float]],
        max_candidates: int,
    ) -> List[Any]:
        active_locker_booking = exists(
            select(1).where(
                Booking.locker_id == Locker.id,
                Booking.state.in_(ACTIVE_BOOKING_STATES),
            )
        )
        available = (Locker.status == "free") & ~active_locker_booking
        candidate_query = (
            select(
                Location.id,
                Location.name,
                Location.address,
                Location.lat,
                Location.lng,
                func.coalesce(
                    func.sum(case((available & (Locker.size == "M"), 1), else_=0)), 0
                ).label("free_medium"),
                func.coalesce(
                    func.sum(case((available & (Locker.size == "L"), 1), else_=0)), 0
                ).label("free_large"),
            )
            .outerjoin(Locker, Locker.location_id == Location.id)
            .where(
                Location.lat >= min_latitude,
                Location.lat <= max_latitude,
                or_(
                    *[
                        and_(Location.lng >= min_lng, Location.lng <= max_lng)
                        for min_lng, max_lng in longitude_ranges
                    ]
                ),
            )
            .group_by(Location.id)
            .limit(max_candidates + 1)
        )
        return (await self.session.execute(candidate_query)).all()

    async def get_location_by_id(self, location_id: int) -> Optional[Any]:
        query = select(Location).where(Location.id == location_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_lockers_by_location(self, location_id: int) -> List[Any]:
        active_locker_booking = exists(
            select(1).where(
                Booking.locker_id == Locker.id,
                Booking.state.in_(ACTIVE_BOOKING_STATES),
            )
        )
        query = (
            select(
                Locker.locker_code,
                Locker.size,
                case(
                    (Locker.status == "offline", "offline"),
                    ((Locker.status == "free") & ~active_locker_booking, "free"),
                    else_="held",
                ).label("status"),
            )
            .where(Locker.location_id == location_id)
            .order_by(Locker.locker_code)
        )
        return (await self.session.execute(query)).all()
