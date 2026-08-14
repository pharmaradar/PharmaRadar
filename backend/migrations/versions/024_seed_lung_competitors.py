"""Seed the three tracked competitors, scoped to lung cancer in French

Revision ID: 024
Revises: 023
Create Date: 2026-08-12

The client asked for AstraZeneca, MSD and BMS to be tracked, "strictly focused on
these specific companies' messaging regarding lung cancer, exclusively in French".

Both halves of that constraint are data on the target rather than convention:

    disease_area = 'lung_cancer'  -> services/fr_sources.focus_clause puts a
                                     lung-cancer term in every search query
    source_scope = 'fr'           -> French locale + curated French source groups

known_urls are the companies' addressable FRENCH properties, verified on
2026-08-12. Two of the three have no French domain of their own — AstraZeneca
France lives under astrazeneca.com/content/az-fr and BMS France under
bms.com/fr — so their LinkedIn France pages and bmsmedinfo.fr carry the French
signal instead. Adding the bare global domains would pull in worldwide corporate
news, which is what the client explicitly does not want.

Idempotent: matches on name and leaves an existing row's edits alone apart from
enforcing the two scope columns.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

DISEASE_AREA = "lung_cancer"

COMPETITORS = [
    {
        "name": "AstraZeneca France",
        "known_urls": [
            "https://www.astrazeneca.com/content/az-fr.html",
            "https://fr.linkedin.com/company/astrazeneca",
            "https://www.astrazeneca.fr",
        ],
        "notes": "Competitor — lung cancer messaging in France (Imfinzi, Tagrisso).",
    },
    {
        "name": "MSD France",
        "known_urls": [
            "https://www.msd-france.com",
            "https://www.linkedin.com/company/msd-france",
        ],
        "notes": "Competitor — lung cancer messaging in France (Keytruda).",
    },
    {
        "name": "Bristol Myers Squibb France",
        "known_urls": [
            "https://www.bms.com/fr",
            "https://www.bmsmedinfo.fr",
            "https://fr.linkedin.com/company/bristol-myers-squibb",
        ],
        "notes": "Competitor — lung cancer messaging in France (Opdivo, Yervoy).",
    },
]


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    try:
        if "targets" not in insp.get_table_names():
            return
    except Exception:
        return

    # source_scope arrives in 023; guard so this migration is safe on a DB where
    # that column was rolled back.
    has_scope = _has_column("targets", "source_scope")

    for row in COMPETITORS:
        existing = conn.execute(
            sa.text("SELECT id FROM targets WHERE lower(name) = lower(:name)"),
            {"name": row["name"]},
        ).fetchone()

        if existing:
            # Do not clobber a user's edits — only enforce the two scope columns.
            sets = ["disease_area = :area", "target_type = 'competitor'"]
            params = {"area": DISEASE_AREA, "id": existing[0]}
            if has_scope:
                sets.append("source_scope = 'fr'")
            conn.execute(
                sa.text(f"UPDATE targets SET {', '.join(sets)} WHERE id = :id"), params
            )
            continue

        columns = ["name", "known_urls", "notes", "disease_area", "target_type", "active"]
        values = [":name", ":known_urls", ":notes", ":area", "'competitor'", "true"]
        params = {
            "name": row["name"],
            "known_urls": json.dumps(row["known_urls"]),
            "notes": row["notes"],
            "area": DISEASE_AREA,
        }
        if has_scope:
            columns.append("source_scope")
            values.append("'fr'")
        conn.execute(
            sa.text(
                f"INSERT INTO targets ({', '.join(columns)}) VALUES ({', '.join(values)})"
            ),
            params,
        )


def downgrade():
    """Remove only the rows this migration created, and only if they have no
    scraped content — deleting a target with posts would orphan them."""
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    try:
        if "targets" not in insp.get_table_names():
            return
    except Exception:
        return

    for row in COMPETITORS:
        conn.execute(
            sa.text(
                "DELETE FROM targets WHERE lower(name) = lower(:name) "
                "AND NOT EXISTS (SELECT 1 FROM scraped_posts WHERE target_id = targets.id)"
            ),
            {"name": row["name"]},
        )
