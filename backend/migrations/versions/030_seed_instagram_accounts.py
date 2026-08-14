"""Seed verified French Instagram accounts

Revision ID: 030
Revises: 029
Create Date: 2026-08-12

Instagram became account-scrapable once the profile actor was wired in, so the
registry can finally hold Instagram entries that actually get collected.

Only handles confirmed to exist are seeded. Instagram serves a login wall to
unauthenticated requests, so fetching the profile page proves nothing — a
missing handle returns the same page as a real one. These were verified through
the search index instead (`site:instagram.com/<handle>` returning that account's
own URLs), with a deliberately fake handle as a control to prove the check
discriminates.

Handles that could NOT be confirmed this way are deliberately absent rather than
guessed: gustaveroussy, institutcurie, rochefrance. Add them from the Tracked
Accounts UI once someone confirms them by eye.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None

_SEED = [
    ("unicancer", "Unicancer", "institution"),
    ("liguecontrelecancer", "Ligue contre le cancer", "patient_association"),
    ("fondationarc", "Fondation ARC", "patient_association"),
    ("bms_france", "BMS France", "pharma"),
]


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    if not _has_table("tracked_accounts"):
        return
    conn = op.get_bind()
    insert = sa.text(
        "INSERT INTO tracked_accounts (platform, handle, url, label, category, active) "
        "VALUES ('instagram', :handle, :url, :label, :category, true) "
        "ON CONFLICT (platform, handle) DO NOTHING"
    )
    for handle, label, category in _SEED:
        conn.execute(insert, {
            "handle": handle, "url": f"https://www.instagram.com/{handle}/",
            "label": label, "category": category,
        })


def downgrade():
    if not _has_table("tracked_accounts"):
        return
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM tracked_accounts WHERE platform = 'instagram' AND handle IN :handles")
        .bindparams(sa.bindparam("handles", expanding=True)),
        {"handles": [h for h, _, _ in _SEED]},
    )
