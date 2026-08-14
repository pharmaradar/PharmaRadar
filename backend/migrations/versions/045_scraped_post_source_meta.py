"""Keep the structured metadata that came with a document.

Publications and trials arrive with facts no amount of LLM reading can recover
from the text: the co-author list, the citation count, the journal, the trial
phase and its NCT id. The first version of the literature lane dropped all of
it, keeping only title and abstract.

Co-authors matter most. The spec asks to identify "the main speaker for topic X
that could be outside our current audience" — a KOL's collaborators ARE that
list, already ranked by how often they publish together.

Stored as JSON rather than columns because the shape differs per lane (a trial
has a phase, a paper has citations) and neither is queried relationally.

Revision ID: 045
Revises: 044
"""
from alembic import op
import sqlalchemy as sa

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scraped_posts", sa.Column("source_meta", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scraped_posts", "source_meta")
