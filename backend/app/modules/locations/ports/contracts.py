"""Abstract repository interfaces for Locations and Lockers (DIP & ISP)."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple


class LocationRepositoryPort(ABC):
    """Port for querying locations and their spatial availability."""

    @abstractmethod
    async def find_nearby_candidates(
        self,
        min_latitude: float,
        max_latitude: float,
        longitude_ranges: List[Tuple[float, float]],
        max_candidates: int,
    ) -> List[Any]:
        """Fetch location candidate rows within bounding box."""
        pass

    @abstractmethod
    async def get_location_by_id(self, location_id: int) -> Optional[Any]:
        """Fetch location by ID."""
        pass

    @abstractmethod
    async def get_lockers_by_location(self, location_id: int) -> List[Any]:
        """Fetch all physical lockers associated with a location."""
        pass
