"""Cache the per-post analysis on the post.

Analysing one post costs an LLM call. Opening the same post twice should not pay
twice, so the sections live on the row and are only rewritten on request.

Separate from `llm_description`, which holds the older two-part
what/so-what describe: that stays as-is so existing describe panels keep working.

Revision ID: 042
Revises: 041
"""
from alembic import op
import sqlalchemy as sa

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON: {exec_summary, so_what, what_is_said, voice{}, reach{}, subtopics[]}
    op.add_column("social_posts", sa.Column("analysis_sections", sa.Text(), nullable=True))
    op.add_column("social_posts",
                  sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("social_posts", "analysed_at")
    op.drop_column("social_posts", "analysis_sections")
