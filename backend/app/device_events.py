"""Handles locker -> cloud events (REQ-LOCK-003/004/006)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Booking, Locker, LockerEvent
from .state_machine import InvalidTransition, State, TERMINAL, apply

ACTIVE = (State.PAID, State.STORED, State.OVERSTAY)
EVT_TO_EVENT = {"door_closed": "door_closed", "pickup_completed": "pickup_completed"}


async def handle_device_event(session: AsyncSession, device_id: str, payload: dict) -> None:
    evt = payload.get("evt")
    async with session.begin():
        locker = (await session.execute(
            select(Locker).where(Locker.device_id == device_id).with_for_update())).scalar_one_or_none()
        if locker is None:
            return
        booking = (await session.execute(
            select(Booking).where(Booking.locker_id == locker.id, Booking.state.in_(ACTIVE))
            .with_for_update())).scalars().first()
        session.add(LockerEvent(locker_id=locker.id, booking_id=booking.id if booking else None,
                                type=str(evt), payload=payload))
        if booking and evt in EVT_TO_EVENT:
            try:
                booking.state = apply(booking.state, EVT_TO_EVENT[evt])
            except InvalidTransition:
                return  # e.g. door_closed while already STORED: audit only
            if booking.state in TERMINAL:
                locker.status = "free"
