"""Add language to social_posts & discovery_results; llm_description to discovery_results

Revision ID: 012
Revises: 011
Create Date: 2026-05-28

Idempotent — guarded by inspector check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    if not _col_exists("social_posts", "language"):
        op.add_column("social_posts", sa.Column("language", sa.String(8), nullable=True))

    if not _col_exists("discovery_results", "language"):
        op.add_column("discovery_results", sa.Column("language", sa.String(8), nullable=True))

    if not _col_exists("discovery_results", "llm_description"):
        op.add_column("discovery_results", sa.Column("llm_description", sa.Text, nullable=True))


def downgrade():
    op.drop_column("social_posts", "language")
    op.drop_column("discovery_results", "language")
    op.drop_column("discovery_results", "llm_description")
