"""Add twitter_handle and linkedin_url to targets

Revision ID: 011
Revises: 010
Create Date: 2026-05-28

Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "targets", "twitter_handle"):
        op.add_column("targets", sa.Column("twitter_handle", sa.String(128), nullable=True))

    if not _has_column(insp, "targets", "linkedin_url"):
        op.add_column("targets", sa.Column("linkedin_url", sa.String(512), nullable=True))


def downgrade():
    op.drop_column("targets", "linkedin_url")
    op.drop_column("targets", "twitter_handle")
