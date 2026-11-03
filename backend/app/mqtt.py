"""Implements specs/mqtt-contract.md."""
import json
import logging
import uuid
from datetime import datetime, timezone

from .config import settings

log = logging.getLogger("mqtt")


def _client():
    import aiomqtt
    return aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                          username=settings.mqtt_username or None,
                          password=settings.mqtt_password or None)


async def publish_unlock(device_id: str, booking_public_id: str) -> str:
    cmd_id = str(uuid.uuid4())
    payload = {"cmd_id": cmd_id, "cmd": "unlock", "booking": booking_public_id,
               "issued_at": datetime.now(timezone.utc).isoformat(), "ttl_s": 30}
    if not settings.mqtt_host:
        log.warning("MQTT disabled; would publish to lockers/%s/cmd: %s", device_id, payload)
        return cmd_id
    async with _client() as c:
        await c.publish(f"lockers/{device_id}/cmd", json.dumps(payload), qos=1)
    return cmd_id
