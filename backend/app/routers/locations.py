import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import rate_limited
from ..db import get_session
from ..models import Booking, Locker, Location
from .auth import current_user_id

router = APIRouter(prefix="/v1/locations", tags=["locations"])
ACTIVE_BOOKING_STATES = ("CREATED", "PAID", "STORED", "OVERSTAY", "DISPUTED")

EARTH_RADIUS_METERS = 6_371_008.8
DEFAULT_RADIUS_METERS = 1_000
MAX_RADIUS_METERS = 1_000
MAX_RESULTS = 100
MAX_CANDIDATES = 5_000
DISCOVERY_REQUESTS_PER_MINUTE = 6_000


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


def _distance_meters(latitude: float, longitude: float, other_latitude: float, other_longitude: float) -> float:
    latitude_delta = math.radians(other_latitude - latitude)
    longitude_delta = math.radians(other_longitude - longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(latitude))
        * math.cos(math.radians(other_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, haversine)))


def _bounding_box(
    latitude: float, longitude: float, radius_m: int
) -> tuple[float, float, list[tuple[float, float]]]:
    latitude_delta = math.degrees(radius_m / EARTH_RADIUS_METERS)
    min_latitude = max(-90.0, latitude - latitude_delta)
    max_latitude = min(90.0, latitude + latitude_delta)
    if min_latitude <= -90.0 or max_latitude >= 90.0:
        return min_latitude, max_latitude, [(-180.0, 180.0)]
    longitude_delta = min(
        180.0,
        math.degrees(radius_m / (EARTH_RADIUS_METERS * math.cos(math.radians(latitude)))),
    )
    if longitude_delta >= 180.0:
        return min_latitude, max_latitude, [(-180.0, 180.0)]
    min_longitude = longitude - longitude_delta
    max_longitude = longitude + longitude_delta
    if min_longitude < -180.0:
        longitude_ranges = [(-180.0, max_longitude), (min_longitude + 360.0, 180.0)]
    elif max_longitude > 180.0:
        longitude_ranges = [(-180.0, max_longitude - 360.0), (min_longitude, 180.0)]
    else:
        longitude_ranges = [(min_longitude, max_longitude)]
    return min_latitude, max_latitude, longitude_ranges


@router.post("/nearby", response_model=NearbyLocationsResponse)
async def list_nearby_locations(
    body: NearbySearch,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> NearbyLocationsResponse:
    client_ip = request.client.host if request.client else "unknown"
    if rate_limited(
        f"nearby:{client_ip}",
        DISCOVERY_REQUESTS_PER_MINUTE,
        60,
    ):
        raise HTTPException(429, "LOCATION_SEARCH_RATE_LIMITED")

    min_latitude, max_latitude, longitude_ranges = _bounding_box(
        body.latitude, body.longitude, body.radius_m
    )
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
                    and_(Location.lng >= min_longitude, Location.lng <= max_longitude)
                    for min_longitude, max_longitude in longitude_ranges
                ]
            ),
        )
        .group_by(Location.id)
        .limit(MAX_CANDIDATES + 1)
    )
    rows = (await session.execute(candidate_query)).all()
    if len(rows) > MAX_CANDIDATES:
        raise HTTPException(503, "LOCATION_SEARCH_AREA_TOO_DENSE")

    found = []
    for row in rows:
        distance = _distance_meters(body.latitude, body.longitude, row.lat, row.lng)
        if distance <= body.radius_m:
            found.append(
                NearbyLocation(
                    location_id=row.id,
                    name=row.name,
                    address=row.address,
                    latitude=row.lat,
                    longitude=row.lng,
                    distance_m=round(distance),
                    available_by_size=LockerAvailability(M=row.free_medium, L=row.free_large),
                )
            )

    found.sort(key=lambda location: (location.distance_m, location.location_id))
    response.headers["Cache-Control"] = "private, no-store"
    return NearbyLocationsResponse(
        items=found[:MAX_RESULTS],
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/{location_id}/lockers", response_model=LocationLockersResponse)
async def list_location_lockers(
    location_id: int,
    _user_id: int = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
) -> LocationLockersResponse:
    location = await session.get(Location, location_id)
    if location is None:
        raise HTTPException(404, "LOCATION_NOT_FOUND")
    active_booking = exists(
        select(1).where(
            Booking.locker_id == Locker.id,
            Booking.state.in_(ACTIVE_BOOKING_STATES),
        )
    )
    lockers = (
        await session.execute(
            select(
                Locker.locker_code,
                Locker.size,
                case(((Locker.status == "free") & active_booking, "held"), else_=Locker.status),
            )
            .where(Locker.location_id == location_id)
            .order_by(Locker.locker_code)
        )
    ).all()
    return LocationLockersResponse(
        location_id=location_id,
        location_name=location.name,
        address=location.address,
        items=[
            LocationLocker(locker_code=code, size=size, status=status)
            for code, size, status in lockers
        ],
    )
