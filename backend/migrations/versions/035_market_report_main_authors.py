"""Store the main voices with the market report that found them.

The "emerging voices" side panel showed these authors next to the report rather
than inside it, so it read as decoration and the client asked for it to go. The
information itself is a spec requirement — the main speakers on a topic, and
specifically the ones outside the tracked audience — so it moves into the report
body and has to be persisted alongside it.

Revision ID: 035
Revises: 034
"""
from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON text, matching how key_posts/sources are already stored on this table.
    op.add_column("market_reports", sa.Column("main_authors", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("market_reports", "main_authors")
