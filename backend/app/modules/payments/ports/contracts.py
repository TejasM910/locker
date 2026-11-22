"""Abstract contracts for Payment Gateway and Webhook Verification (DIP)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class PaymentGatewayPort(ABC):
    """Port for communicating with payment processing networks."""

    @abstractmethod
    def create_order(self, amount_paise: int, receipt: str, notes: Dict[str, Any]) -> str:
        """Create order at gateway and return order id."""
        pass

    @abstractmethod
    def verify_webhook_signature(self, body_bytes: bytes, signature: str) -> bool:
        """Verify webhook signature from gateway."""
        pass
