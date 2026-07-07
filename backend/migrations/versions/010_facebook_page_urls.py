"""Add facebook_page_urls column to app_settings

Revision ID: 010
Revises: 009
Create Date: 2026-05-28

Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "app_settings", "facebook_page_urls"):
        op.add_column("app_settings", sa.Column("facebook_page_urls", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("app_settings", "facebook_page_urls")
