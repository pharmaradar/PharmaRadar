"""Store the full market-research analysis for a tracked account.

The first version of this produced a summary, a "so what" and a list of themes.
The client asked for the same six-section market-research shape used everywhere
else — executive summary, so what, what is being said, voice distribution,
volume of mentions, key sub-topics — so the whole structure is kept as one JSON
document rather than a column per section.

The earlier columns stay: they are what the account CARD renders, and splitting
that out of JSON on every list request would be wasted work.

Revision ID: 040
Revises: 039
"""
from alembic import op
import sqlalchemy as sa

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON: {exec_summary, so_what, what_is_said, voices_note, volume_note,
    #        subtopics[], voice_rows[], volume{}, voice_exact_share, item_count}
    op.add_column("tracked_accounts", sa.Column("analysis_sections", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_accounts", "analysis_sections")
