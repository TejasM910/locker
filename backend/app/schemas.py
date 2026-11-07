from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator


class BookingCreate(BaseModel):
    locker_code: str | None = Field(default=None, min_length=3, max_length=16)
    location_id: int | None = Field(default=None, ge=1)
    size: str | None = Field(default=None, pattern="^(M|L)$")
    duration_hours: int = Field(ge=1, le=72)

    @model_validator(mode="after")
    def require_locker_selection(self) -> Self:
        if self.locker_code is None and (self.location_id is None or self.size is None):
            raise ValueError("Provide locker_code or both location_id and size.")
        return self


class BookingOut(BaseModel):
    public_id: str
    state: str
    locker_code: str
    size: str
    amount_paise: int
    duration_hours: int
    razorpay_order_id: str | None
    expires_at: datetime
