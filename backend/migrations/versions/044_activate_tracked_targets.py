"""Switch the tracked KOLs and competitors back on.

18 of 19 targets sat at active=false, so every scrape run processed one company
and the KOL corpus was 27 documents from 2 targets. That single fact explained
most of "the reports are not detailed enough" — no prompt or format change can
compensate for an empty corpus.

The publication and trial lanes now feed these targets from free official APIs,
so activation no longer implies a large scraping bill: the expensive TinyFish
web lane is only exercised when a scrape run is actually triggered.

The `(test)` target stays off — it is scaffolding, not a real company.

Revision ID: 044
Revises: 043
"""
from alembic import op
import sqlalchemy as sa

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        UPDATE targets
           SET active = true
         WHERE active = false
           AND name NOT ILIKE '%(test)%'
    """))
    print(f"  activated {result.rowcount} targets")


def downgrade() -> None:
    # Deliberately not reversed: which targets a client watches is their
    # decision, and re-disabling them wholesale would discard it.
    pass
