"""Add discovery_results table

Revision ID: 006
Revises: 005
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discovery_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query", sa.String(512), nullable=False, index=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("published_date", sa.String(32), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True, unique=True, index=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("discovery_results")
