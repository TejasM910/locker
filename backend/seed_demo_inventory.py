from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.locker_codes import locker_code, location_code_prefix
from app.models import Locker, Location

DEMO_LOCATIONS = (
    {
        "name": "DEMO ONLY - Bengaluru Central",
        "address": "Fictional demo locker location",
        "lat": 12.9716,
        "lng": 77.5946,
        "device_prefix": "demo-map-central",
    },
    {
        "name": "DEMO ONLY - Cubbon Park",
        "address": "Fictional demo locker location",
        "lat": 12.9743,
        "lng": 77.5946,
        "device_prefix": "demo-map-cubbon",
    },
    {
        "name": "DEMO ONLY - Church Street",
        "address": "Fictional demo locker location",
        "lat": 12.9716,
        "lng": 77.5980,
        "device_prefix": "demo-map-church",
    },
)
LOCKERS_PER_LOCATION = (("M", 1), ("M", 2), ("L", 1))


def main() -> None:
    database_url = make_url(settings.sync_database_url)
    if database_url.get_backend_name() != "mysql" or database_url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise SystemExit("Demo seeding is restricted to a local MySQL database.")

    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session, session.begin():
            used_prefixes: set[str] = set()
            prefixes_by_name = {}
            for name in session.scalars(select(Location.name).order_by(Location.id)):
                prefix = location_code_prefix(name, used_prefixes)
                used_prefixes.add(prefix)
                prefixes_by_name[name] = prefix
            for definition in DEMO_LOCATIONS:
                if definition["name"] not in prefixes_by_name:
                    prefix = location_code_prefix(definition["name"], used_prefixes)
                    used_prefixes.add(prefix)
                    prefixes_by_name[definition["name"]] = prefix
            next_code_by_name = {}
            for name in prefixes_by_name:
                prefix = prefixes_by_name[name]
                codes = session.scalars(
                    select(Locker.locker_code).join(Location).where(Location.name == name)
                )
                used_sequences = [
                    int(code[len(prefix):])
                    for code in codes
                    if code.startswith(prefix) and code[len(prefix):].isdigit()
                ]
                next_code_by_name[name] = max(used_sequences, default=100) + 1

            for definition in DEMO_LOCATIONS:
                location = session.execute(
                    select(Location).where(Location.name == definition["name"])
                ).scalar_one_or_none()
                if location is None:
                    location = Location(
                        name=definition["name"],
                        address=definition["address"],
                        lat=definition["lat"],
                        lng=definition["lng"],
                    )
                    session.add(location)
                    session.flush()
                elif (
                    location.address != definition["address"]
                    or location.lat != definition["lat"]
                    or location.lng != definition["lng"]
                ):
                    raise RuntimeError(
                        f"Existing demo location {definition['name']!r} differs from the seed data; "
                        "no records were changed."
                    )

                for sequence, (size, index) in enumerate(LOCKERS_PER_LOCATION, start=101):
                    device_id = f"{definition['device_prefix']}-{size.lower()}-{index}"
                    locker = session.execute(
                        select(Locker).where(Locker.device_id == device_id)
                    ).scalar_one_or_none()
                    if locker is None:
                        prefix = prefixes_by_name[location.name]
                        sequence = next_code_by_name[location.name]
                        next_code_by_name[location.name] += 1
                        session.add(
                            Locker(
                                location_id=location.id,
                                size=size,
                                locker_code=locker_code(prefix, sequence),
                                device_id=device_id,
                                status="free",
                            )
                        )
                    elif locker.location_id != location.id or locker.size != size:
                        raise RuntimeError(
                            f"Demo device ID {device_id!r} is already assigned elsewhere; "
                            "no records were changed."
                        )

                session.flush()
                counts = session.execute(
                    select(Locker.size, Locker.status)
                    .where(Locker.location_id == location.id)
                ).all()
                free_by_size = {
                    size: sum(1 for row_size, status in counts if row_size == size and status == "free")
                    for size in ("M", "L")
                }
                print(
                    f"{location.id}: {location.name} "
                    f"({free_by_size['M']} free medium, {free_by_size['L']} free large)"
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
