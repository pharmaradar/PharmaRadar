"""Add media_type and thumbnail_url to discovery_results

Revision ID: 007
Revises: 006
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("discovery_results", sa.Column("media_type", sa.String(32), nullable=False, server_default="article"))
    op.add_column("discovery_results", sa.Column("thumbnail_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("discovery_results", "thumbnail_url")
    op.drop_column("discovery_results", "media_type")
