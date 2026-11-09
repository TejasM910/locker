"""Persist idempotency keys for booking hold requests.

Revision ID: 20261109_0004
Revises: 20261107_0003
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20261109_0004"
down_revision = "20261107_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_booking_request_user_key"),
        sa.UniqueConstraint("booking_id", name="uq_booking_request_booking"),
    )


def downgrade() -> None:
    op.drop_table("booking_requests")
