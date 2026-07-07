"""Remove chroma_id from scraped_posts

Revision ID: 005
Revises: 004
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("scraped_posts", "chroma_id")


def downgrade():
    op.add_column("scraped_posts", sa.Column("chroma_id", sa.String(128), nullable=True, index=True))
