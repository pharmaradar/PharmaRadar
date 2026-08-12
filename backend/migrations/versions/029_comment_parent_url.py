"""Instagram comments — parent post link on social_posts

Revision ID: 029
Revises: 028
Create Date: 2026-08-12

The client asked for comment analysis ("patient comment following an Instagram
post"). Comments are stored as social_posts rows with kind='comment' rather than
in their own table, so they inherit adverse-event classification, the dedup hash,
language detection and every read-time filter automatically. A parallel table
would have been the easier build and the one most likely to quietly miss the
pharmacovigilance guarantee.

`parent_url` is the post the comment was left under.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_column("social_posts", "parent_url"):
        op.add_column("social_posts", sa.Column("parent_url", sa.Text(), nullable=True))
        op.create_index("ix_social_posts_parent_url", "social_posts", ["parent_url"])


def downgrade():
    if _has_column("social_posts", "parent_url"):
        op.drop_index("ix_social_posts_parent_url", table_name="social_posts")
        op.drop_column("social_posts", "parent_url")
