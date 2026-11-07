"""Index location coordinates for bounded nearby searches.

Revision ID: 20261105_0002
Revises: 20261104_0001
Create Date: 2026-11-05
"""

from alembic import op


revision = "20261105_0002"
down_revision = "20261104_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_locations_lat_lng", "locations", ["lat", "lng"])


def downgrade() -> None:
    op.drop_index("ix_locations_lat_lng", table_name="locations")
