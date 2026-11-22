"""Location discovery and inventory endpoints with clean architecture."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import rate_limited
from app.db import get_session
from app.routers.auth import current_user_id
from ..domain.geo import calculate_bounding_box, calculate_distance_meters, DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS
from ..ports.contracts import LocationRepositoryPort
from ..adapters.repository import SqlAlchemyLocationRepository

router = APIRouter(prefix="/v1/locations", tags=["locations"])

MAX_RESULTS = 100
MAX_CANDIDATES = 5_000
DISCOVERY_REQUESTS_PER_MINUTE = 6_000


def get_location_repository(session: AsyncSession = Depends(get_session)) -> LocationRepositoryPort:
    return SqlAlchemyLocationRepository(session)


class NearbySearch(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius_m: int = Field(default=DEFAULT_RADIUS_METERS, ge=500, le=MAX_RADIUS_METERS)


class LockerAvailability(BaseModel):
    M: int = Field(ge=0)
    L: int = Field(ge=0)


class NearbyLocation(BaseModel):
    location_id: int
    name: str
    address: str
    latitude: float
    longitude: float
    distance_m: int
    available_by_size: LockerAvailability


class NearbyLocationsResponse(BaseModel):
    items: list[NearbyLocation]
    generated_at: datetime


class LocationLocker(BaseModel):
    locker_code: str
    size: str
    status: str


class LocationLockersResponse(BaseModel):
    location_id: int
    location_name: str
    address: str
    items: list[LocationLocker]


@router.post("/nearby", response_model=NearbyLocationsResponse)
async def list_nearby_locations(
    body: NearbySearch,
    request: Request,
    response: Response,
    repo: LocationRepositoryPort = Depends(get_location_repository),
) -> NearbyLocationsResponse:
    client_ip = request.client.host if request.client else "unknown"
    if rate_limited(
        f"nearby:{client_ip}",
        DISCOVERY_REQUESTS_PER_MINUTE,
        60,
    ):
        raise HTTPException(429, "LOCATION_SEARCH_RATE_LIMITED")

    min_latitude, max_latitude, longitude_ranges = calculate_bounding_box(
        body.latitude, body.longitude, body.radius_m
    )

    rows = await repo.find_nearby_candidates(
        min_latitude, max_latitude, longitude_ranges, MAX_CANDIDATES
    )
    if len(rows) > MAX_CANDIDATES:
        raise HTTPException(503, "LOCATION_SEARCH_AREA_TOO_DENSE")

    found = []
    for row in rows:
        distance = calculate_distance_meters(body.latitude, body.longitude, row.lat, row.lng)
        if distance <= body.radius_m:
            found.append(
                (
                    distance,
                    NearbyLocation(
                        location_id=row.id,
                        name=row.name,
                        address=row.address,
                        latitude=row.lat,
                        longitude=row.lng,
                        distance_m=int(round(distance)),
                        available_by_size=LockerAvailability(
                            M=int(row.free_medium),
                            L=int(row.free_large),
                        ),
                    ),
                )
            )

    found.sort(key=lambda item: (item[0], item[1].location_id))
    response.headers["Cache-Control"] = "no-store"
    return NearbyLocationsResponse(
        items=[location for _, location in found[:MAX_RESULTS]],
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/{location_id}/lockers", response_model=LocationLockersResponse)
async def list_location_lockers(
    location_id: int,
    _user_id: int = Depends(current_user_id),
    repo: LocationRepositoryPort = Depends(get_location_repository),
) -> LocationLockersResponse:
    location = await repo.get_location_by_id(location_id)
    if location is None:
        raise HTTPException(404, "LOCATION_NOT_FOUND")

    lockers = await repo.get_lockers_by_location(location_id)
    return LocationLockersResponse(
        location_id=location.id,
        location_name=location.name,
        address=location.address,
        items=[
            LocationLocker(
                locker_code=item.locker_code,
                size=item.size,
                status=item.status,
            )
            for item in lockers
        ],
    )
