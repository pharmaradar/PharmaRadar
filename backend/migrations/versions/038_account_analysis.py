"""Store the AI read of a tracked account alongside the account.

A per-account analysis costs an LLM call over that account's posts. Recomputing
it every time the client opens a row would charge for the same answer
repeatedly, so it is cached here and only refreshed when asked or when new posts
arrive (tracked via `analysis_post_count`).

Revision ID: 038
Revises: 037
"""
from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_accounts", sa.Column("analysis_summary", sa.Text(), nullable=True))
    op.add_column("tracked_accounts", sa.Column("analysis_so_what", sa.Text(), nullable=True))
    # JSON list[str] — the themes this account keeps returning to.
    op.add_column("tracked_accounts", sa.Column("analysis_themes", sa.Text(), nullable=True))
    op.add_column("tracked_accounts",
                  sa.Column("analysis_at", sa.DateTime(timezone=True), nullable=True))
    # How many posts the cached analysis was written from. When the account has
    # gained posts since, the UI can offer a refresh instead of showing a read
    # of material that is now incomplete.
    op.add_column("tracked_accounts",
                  sa.Column("analysis_post_count", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade() -> None:
    for column in ("analysis_post_count", "analysis_at", "analysis_themes",
                   "analysis_so_what", "analysis_summary"):
        op.drop_column("tracked_accounts", column)
