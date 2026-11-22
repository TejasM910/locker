"""Abstract contracts for Device / IoT Gateway (DIP)."""

from abc import ABC, abstractmethod


class MqttPublisherPort(ABC):
    """Port for publishing commands to physical lockers."""

    @abstractmethod
    def publish_unlock_command(self, device_id: str, command_id: str) -> bool:
        """Publish unlock command to topic lockers/{device_id}/cmd."""
        pass
