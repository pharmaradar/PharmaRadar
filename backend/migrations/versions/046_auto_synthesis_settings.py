"""Let syntheses refresh themselves instead of waiting for a click.

Every dashboard synthesis — KOL, competitor, comprehensive, global — was
generate-on-demand, so after a pipeline run the client had to press four
buttons to see the new data reflected. Anything they did not press showed
yesterday's analysis over today's corpus, with no indication it was stale.

Two switches rather than one: a daily refresh for routine currency, and a
post-run refresh so a manual pipeline run is immediately readable. They are
independent because a client who scrapes weekly still wants the daily view
current, and one who scrapes constantly may not want an LLM bill per run.

Revision ID: 046
Revises: 045
"""
from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Off by default: each refresh costs several LLM calls, so switching it on
    # is the client's decision, not a surprise on their next bill.
    op.add_column("app_settings", sa.Column(
        "auto_synthesis_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column(
        "auto_synthesis_hour", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("app_settings", sa.Column(
        "auto_synthesis_after_run", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column(
        "auto_synthesis_last_run", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("auto_synthesis_last_run", "auto_synthesis_after_run",
                   "auto_synthesis_hour", "auto_synthesis_enabled"):
        op.drop_column("app_settings", column)
