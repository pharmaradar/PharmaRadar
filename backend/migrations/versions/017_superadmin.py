"""Add is_superadmin flag + mark the bootstrap admin as protected super admin

Revision ID: 017
Revises: 016
Create Date: 2026-05-29

The super admin cannot be deleted, deactivated, or demoted by anyone.
Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine.reflection import Inspector

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

SUPERADMIN_EMAIL = "admin@pharmaradar.com"


def _has_col(table: str, col: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return col in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_col("users", "is_superadmin"):
        op.add_column("users", sa.Column("is_superadmin", sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
    # Promote the bootstrap admin to protected super admin
    op.get_bind().execute(
        text("UPDATE users SET is_superadmin = true WHERE email = :e"),
        {"e": SUPERADMIN_EMAIL},
    )


def downgrade():
    if _has_col("users", "is_superadmin"):
        op.drop_column("users", "is_superadmin")
