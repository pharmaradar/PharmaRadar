"""Competitor tracking — target_type on targets

Revision ID: 021
Revises: 020
Create Date: 2026-07-18

Competitors are the same shape as KOL targets (same scrape/extract/dedup
pipeline) but a different category: 'kol' (default — all existing rows) or
'competitor'. Stored as a plain String like every other enum-ish column in
this codebase (e.g. users.role) — no PG ENUM type to migrate later.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_column("targets", "target_type"):
        op.add_column(
            "targets",
            sa.Column("target_type", sa.String(16), nullable=False, server_default="kol"),
        )
        op.create_index("ix_targets_target_type", "targets", ["target_type"])


def downgrade():
    if _has_column("targets", "target_type"):
        op.drop_index("ix_targets_target_type", table_name="targets")
        op.drop_column("targets", "target_type")
