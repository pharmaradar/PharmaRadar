"""Add social_lang_filter to app_settings

Revision ID: 013
Revises: 012
Create Date: 2026-05-28

Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    if not _col_exists("app_settings", "social_lang_filter"):
        op.add_column("app_settings", sa.Column("social_lang_filter", sa.String(8), nullable=True, server_default="fr"))


def downgrade():
    op.drop_column("app_settings", "social_lang_filter")
