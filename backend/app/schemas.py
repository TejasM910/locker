from datetime import datetime

from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
    location_id: int
    size: str = Field(pattern="^(M|L)$")
    duration_hours: int = Field(ge=1, le=72)


class BookingOut(BaseModel):
    public_id: str
    state: str
    amount_paise: int
    duration_hours: int
    razorpay_order_id: str | None
    expires_at: datetime
