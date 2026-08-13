"""Cache the per-insight analysis on the insight.

Same reasoning as the per-post cache: analysing one insight costs an LLM call,
and opening the dashboard drawer twice should not pay twice.

Revision ID: 043
Revises: 042
"""
from alembic import op
import sqlalchemy as sa

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extracted_insights",
                  sa.Column("analysis_sections", sa.Text(), nullable=True))
    op.add_column("extracted_insights",
                  sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_insights", "analysed_at")
    op.drop_column("extracted_insights", "analysis_sections")
