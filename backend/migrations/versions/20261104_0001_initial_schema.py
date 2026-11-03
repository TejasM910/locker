"""Create the initial locker booking schema.

Revision ID: 20261104_0001
Revises:
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0001"
down_revision = None
branch_labels = None
depends_on = None


booking_state = sa.Enum(
    "CREATED",
    "PAID",
    "STORED",
    "OVERSTAY",
    "RELEASED",
    "EXPIRED",
    "CANCELLED",
    "DISPUTED",
    name="state",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_table(
        "locations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "lockers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("size", sa.String(length=2), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id"),
    )
    op.create_index("ix_lockers_location_id", "lockers", ["location_id"])
    op.create_index("ix_lockers_status", "lockers", ["status"])
    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("locker_id", sa.BigInteger(), nullable=False),
        sa.Column("state", booking_state, nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["locker_id"], ["lockers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("razorpay_order_id"),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])
    op.create_index("ix_bookings_locker_id", "bookings", ["locker_id"])
    op.create_index("ix_bookings_state", "bookings", ["state"])
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=False),
        sa.Column("gateway_payment_id", sa.String(length=64), nullable=False),
        sa.Column("gateway_event_id", sa.String(length=128), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gateway_event_id", name="uq_payment_event"),
    )
    op.create_index("ix_payments_booking_id", "payments", ["booking_id"])
    op.create_table(
        "locker_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("locker_id", sa.BigInteger(), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.ForeignKeyConstraint(["locker_id"], ["lockers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locker_events_locker_id", "locker_events", ["locker_id"])


def downgrade() -> None:
    op.drop_index("ix_locker_events_locker_id", table_name="locker_events")
    op.drop_table("locker_events")
    op.drop_index("ix_payments_booking_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_bookings_state", table_name="bookings")
    op.drop_index("ix_bookings_locker_id", table_name="bookings")
    op.drop_index("ix_bookings_user_id", table_name="bookings")
    op.drop_table("bookings")
    op.drop_index("ix_lockers_status", table_name="lockers")
    op.drop_index("ix_lockers_location_id", table_name="lockers")
    op.drop_table("lockers")
    op.drop_table("locations")
    op.drop_table("users")
