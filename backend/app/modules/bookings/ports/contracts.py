"""Abstract contracts for Booking lifecycle and persistence (DIP & ISP)."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional
from datetime import datetime


class BookingRepositoryPort(ABC):
    """Port for transactional booking queries and reservations."""

    @abstractmethod
    async def get_by_public_id(self, public_id: str) -> Optional[Any]:
        """Retrieve booking by public unguessable identifier."""
        pass

    @abstractmethod
    async def create_booking_hold(
        self,
        user_id: int,
        locker_code: str,
        duration_hours: int,
        amount_paise: int,
        expires_at: datetime,
    ) -> Any:
        """Atomically lock locker with SKIP LOCKED and create CREATED booking."""
        pass

    @abstractmethod
    async def cancel_booking(self, booking_id: int) -> bool:
        """Cancel unpaid booking hold and free locker."""
        pass
