import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, DateTime, Enum as SAEnum, ForeignKey,
                        Index, Integer, String, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .state_machine import State


def utcnow() -> datetime:  # REQ-NFR-001 (naive UTC stored in DATETIME)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_public_id() -> str:  # REQ-BOOK-005
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(3), default=utcnow)


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (Index("ix_locations_lat_lng", "lat", "lng"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255), default="")
    lat: Mapped[float] = mapped_column(default=0.0)
    lng: Mapped[float] = mapped_column(default=0.0)


class Locker(Base):
    __tablename__ = "lockers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    size: Mapped[str] = mapped_column(String(2))  # M | L
    locker_code: Mapped[str] = mapped_column(String(16), unique=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="free", index=True)  # free|held|offline


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, default=new_public_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    locker_id: Mapped[int] = mapped_column(ForeignKey("lockers.id"), index=True)
    state: Mapped[State] = mapped_column(SAEnum(State), default=State.CREATED, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    duration_hours: Mapped[int] = mapped_column(Integer)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(3), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(3))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(3), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(3), nullable=True)
    locker: Mapped[Locker] = relationship(lazy="joined")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("gateway_event_id", name="uq_payment_event"),)  # ADR-3
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    gateway_payment_id: Mapped[str] = mapped_column(String(64))
    gateway_event_id: Mapped[str] = mapped_column(String(128))
    amount_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(3), default=utcnow)


class LockerEvent(Base):  # audit log, REQ-LOCK-006
    __tablename__ = "locker_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    locker_id: Mapped[int] = mapped_column(ForeignKey("lockers.id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(3), default=utcnow)
