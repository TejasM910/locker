import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.models import Base, Locker, Location
from app.routers import locations as locations_router
from app.routers.locations import _bounding_box, _distance_meters


def test_distance_and_search_bounds_handle_the_antimeridian():
    distance = _distance_meters(0, 179.99, 0, -179.99)
    min_latitude, max_latitude, longitude_ranges = _bounding_box(0, 179.99, 5_000)

    assert 2_000 < distance < 2_300
    assert min_latitude < 0 < max_latitude
    assert len(longitude_ranges) == 2
    assert longitude_ranges[0][0] == -180
    assert longitude_ranges[1][1] == 180


@pytest.mark.asyncio
async def test_nearby_locations_are_sorted_and_report_free_inventory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    session.add_all(
        [
            Location(id=1, name="Center", address="Center street", lat=12.9716, lng=77.5946),
            Location(id=2, name="Nearby", address="Nearby street", lat=12.9716, lng=77.6000),
            Location(id=3, name="Outside", address="Far street", lat=12.9716, lng=77.6946),
            Location(id=4, name="Outside 1 km", address="Far street", lat=12.9820, lng=77.5946),
        ]
    )
    session.add_all(
        [
            Locker(id=1, location_id=2, size="M", locker_code="NE101", device_id="near-m-free", status="free"),
            Locker(id=2, location_id=2, size="M", locker_code="NE102", device_id="near-m-held", status="held"),
            Locker(id=3, location_id=2, size="L", locker_code="NE103", device_id="near-l-free", status="free"),
            Locker(id=4, location_id=2, size="L", locker_code="NE104", device_id="near-l-offline", status="offline"),
            Locker(id=5, location_id=4, size="M", locker_code="OU101", device_id="empty-m-held", status="held"),
        ]
    )
    session.commit()

    class AsyncSessionAdapter:
        async def execute(self, query):
            return session.execute(query)

        async def get(self, model, key):
            return session.get(model, key)

    async def override_session():
        yield AsyncSessionAdapter()

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("test-client-nearby", 1234)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/locations/nearby",
                json={"latitude": 12.9716, "longitude": 77.5946},
            )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.request.url.query == b""
        items = response.json()["items"]
        assert [item["location_id"] for item in items] == [1, 2]
        assert items[1]["available_by_size"] == {"M": 1, "L": 1}
        assert all(item["distance_m"] <= 1_000 for item in items)
        assert items[0]["distance_m"] <= items[1]["distance_m"]
    finally:
        app.dependency_overrides.pop(get_session, None)
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_location_locker_inventory_includes_stable_codes_and_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Location(id=1, name="Church Street", address="", lat=12.9, lng=77.6))
    session.add_all(
        [
            Locker(id=1, location_id=1, size="M", locker_code="CH101", device_id="ch-101", status="free"),
            Locker(id=2, location_id=1, size="L", locker_code="CH102", device_id="ch-102", status="held"),
        ]
    )
    session.commit()

    class AsyncSessionAdapter:
        async def execute(self, query):
            return session.execute(query)

        async def get(self, model, key):
            return session.get(model, key)

    async def override_session():
        yield AsyncSessionAdapter()

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/v1/locations/1/lockers", headers={"X-User-Id": "8"})
            missing = await client.get("/v1/locations/999/lockers", headers={"X-User-Id": "8"})
            unauthenticated = await client.get("/v1/locations/1/lockers")
        assert response.status_code == 200
        assert response.json() == {
            "location_id": 1,
            "location_name": "Church Street",
            "address": "",
            "items": [
                {"locker_code": "CH101", "size": "M", "status": "free"},
                {"locker_code": "CH102", "size": "L", "status": "held"},
            ],
        }
        assert missing.status_code == 404
        assert unauthenticated.status_code == 401
    finally:
        app.dependency_overrides.pop(get_session, None)
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_nearby_search_rejects_invalid_coordinates_and_radius():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        latitude_response = await client.post(
            "/v1/locations/nearby",
            json={"latitude": 91, "longitude": 0},
        )
        radius_response = await client.post(
            "/v1/locations/nearby",
            json={"latitude": 0, "longitude": 0, "radius_m": 1_001},
        )

    assert latitude_response.status_code == 422
    assert radius_response.status_code == 422


@pytest.mark.asyncio
async def test_nearby_search_rate_limits_by_client_ip(monkeypatch):
    async def override_session():
        yield object()

    monkeypatch.setattr(locations_router, "rate_limited", lambda *_args: True)
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("limited-nearby-client", 1234)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/locations/nearby",
                json={"latitude": 12.9716, "longitude": 77.5946},
            )
        assert response.status_code == 429
        assert response.json()["detail"] == "LOCATION_SEARCH_RATE_LIMITED"
        assert response.request.url.query == b""
    finally:
        app.dependency_overrides.pop(get_session, None)
