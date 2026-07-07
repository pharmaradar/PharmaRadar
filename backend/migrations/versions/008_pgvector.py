"""pgvector + embedding column + disease_area + likes/views

Revision ID: 008
Revises: 007
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE extracted_insights ADD COLUMN IF NOT EXISTS embedding vector(512)")
    op.add_column("targets", sa.Column("disease_area", sa.String(64), nullable=True))
    op.add_column("scraped_posts", sa.Column("likes", sa.Integer(), nullable=True))
    op.add_column("scraped_posts", sa.Column("views", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("scraped_posts", "views")
    op.drop_column("scraped_posts", "likes")
    op.drop_column("targets", "disease_area")
    op.execute("ALTER TABLE extracted_insights DROP COLUMN IF EXISTS embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
