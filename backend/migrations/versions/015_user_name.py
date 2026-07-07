"""Add name column to users

Revision ID: 015
Revises: 014
Create Date: 2026-05-29

Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return col in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_col("users", "name"):
        op.add_column("users", sa.Column("name", sa.String(120), nullable=True))


def downgrade():
    if _has_col("users", "name"):
        op.drop_column("users", "name")
