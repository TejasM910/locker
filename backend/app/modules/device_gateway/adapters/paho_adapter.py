"""Mosquitto / Paho MQTT client implementation of MqttPublisherPort."""

import json
import logging
from app.config import settings
from ..ports.contracts import MqttPublisherPort

logger = logging.getLogger(__name__)


class PahoMqttPublisherAdapter(MqttPublisherPort):
    def publish_unlock_command(self, device_id: str, command_id: str) -> bool:
        from app.mqtt import publish_unlock
        try:
            publish_unlock(device_id, command_id)
            return True
        except Exception as e:
            logger.error("[PahoMqttPublisherAdapter] Failed to publish unlock to %s: %s", device_id, e)
            return False
