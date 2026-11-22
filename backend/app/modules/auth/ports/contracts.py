"""Abstract contracts (Ports) for the Auth module."""

from abc import ABC, abstractmethod
from typing import Optional


class SmsGatewayPort(ABC):
    """Interface for dispatching SMS messages (DIP & OCP)."""

    @abstractmethod
    async def send_otp(self, phone: str, otp_code: str) -> bool:
        """Send OTP code to recipient's phone number."""
        pass


class OtpServicePort(ABC):
    """Interface for OTP generation and verification."""

    @abstractmethod
    def issue_otp(self, phone: str) -> str:
        """Generate and store OTP."""
        pass

    @abstractmethod
    def verify_otp(self, phone: str, code: str) -> bool:
        """Verify provided OTP code."""
        pass


class TokenServicePort(ABC):
    """Interface for token issuance and resolution."""

    @abstractmethod
    def create_token(self, user_id: int) -> str:
        """Issue access token for user."""
        pass

    @abstractmethod
    def resolve_token(self, token: str) -> Optional[int]:
        """Resolve user_id from access token."""
        pass
