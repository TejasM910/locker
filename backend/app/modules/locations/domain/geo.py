"""Pure domain calculation functions for Geolocation (Haversine & Bounding Box)."""

import math

EARTH_RADIUS_METERS = 6_371_008.8
DEFAULT_RADIUS_METERS = 1_000
MAX_RADIUS_METERS = 1_000


def calculate_distance_meters(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> float:
    """Calculate distance between two GPS coordinates using Haversine formula."""
    latitude_delta = math.radians(other_latitude - latitude)
    longitude_delta = math.radians(other_longitude - longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(latitude))
        * math.cos(math.radians(other_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, haversine)))


def calculate_bounding_box(
    latitude: float, longitude: float, radius_m: int
) -> tuple[float, float, list[tuple[float, float]]]:
    """Calculate bounding box latitude and longitude ranges for spatial indexing."""
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
