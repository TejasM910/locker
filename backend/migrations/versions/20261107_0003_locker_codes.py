"""Add stable public codes to physical lockers.

Revision ID: 20261107_0003
Revises: 20261105_0002
Create Date: 2026-11-07
"""

import re
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "20261107_0003"
down_revision = "20261105_0002"
branch_labels = None
depends_on = None


def _prefix(name: str, used: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z]+", normalized)
    if len(words) >= 2 and words[0].upper() == "DEMO" and words[1].upper() == "ONLY":
        words = words[2:]
    if not words:
        words = ["LK"]
    first = words[0].upper()
    candidates = [first[:length] for length in range(2, len(first) + 1)]
    base = first[:2] or "L"
    candidates.extend(base + word[0].upper() for word in words[1:])
    candidates.extend(f"{base}{suffix}" for suffix in range(2, 1000))
    for candidate in candidates:
        if candidate and candidate not in used:
            return candidate
    raise RuntimeError(f"Could not derive a unique locker-code prefix from {name!r}.")


def upgrade() -> None:
    op.add_column("lockers", sa.Column("locker_code", sa.String(length=16), nullable=True))
    connection = op.get_bind()
    locations = connection.execute(
        sa.text("SELECT id, name FROM locations ORDER BY id")
    ).mappings()
    used_prefixes: set[str] = set()
    for location in locations:
        prefix = _prefix(location["name"], used_prefixes)
        used_prefixes.add(prefix)
        lockers = connection.execute(
            sa.text("SELECT id FROM lockers WHERE location_id = :location_id ORDER BY id"),
            {"location_id": location["id"]},
        ).mappings()
        for index, locker in enumerate(lockers, start=101):
            connection.execute(
                sa.text("UPDATE lockers SET locker_code = :locker_code WHERE id = :locker_id"),
                {
                    "locker_code": f"{prefix}{index:03d}",
                    "locker_id": locker["id"],
                },
            )
    op.alter_column("lockers", "locker_code", existing_type=sa.String(length=16), nullable=False)
    op.create_unique_constraint("uq_lockers_locker_code", "lockers", ["locker_code"])


def downgrade() -> None:
    op.drop_constraint("uq_lockers_locker_code", "lockers", type_="unique")
    op.drop_column("lockers", "locker_code")
