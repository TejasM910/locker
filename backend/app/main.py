import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .booking_expiry import run_booking_expiry_loop
from .config import settings
from .db import SessionLocal, get_session
from .device_events import handle_device_event
from .routers import auth, bookings, webhooks

log = logging.getLogger("app")


def parse_device_payload(payload: bytes) -> dict | None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Ignoring malformed MQTT JSON payload: %s", exc)
        return None
    if not isinstance(data, dict):
        log.warning("Ignoring MQTT payload that is not a JSON object")
        return None
    return data


async def mqtt_listener():
    import aiomqtt
    while True:
        try:
            async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                                      username=settings.mqtt_username or None,
                                      password=settings.mqtt_password or None) as c:
                await c.subscribe("lockers/+/evt", qos=1)
                async for m in c.messages:
                    device_id = str(m.topic).split("/")[1]
                    payload = parse_device_payload(m.payload)
                    if payload is None:
                        continue
                    async with SessionLocal() as s:
                        await handle_device_event(s, device_id, payload)
        except Exception:
            log.exception("mqtt listener error; retrying in 5s")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(mqtt_listener()) if settings.mqtt_host else None
    expiry_task = asyncio.create_task(run_booking_expiry_loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        expiry_task.cancel()
        with suppress(asyncio.CancelledError):
            await expiry_task


app = FastAPI(title="Locker API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth.router)
app.include_router(bookings.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):  # REQ-OPS-001
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
