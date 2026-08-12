"""Re-score language on existing posts + seed French clinical keywords

Revision ID: 022
Revises: 021
Create Date: 2026-08-10

The old FR/EN heuristic required >= 4 French function words in the first 1000
characters — an absolute threshold with no comparison against English. Anything
shorter than a couple of paragraphs (i.e. almost every social post) fell under
it and was stored as 'en'. With the French filter on, those posts were hidden.

Fixing the detector only affects *new* rows, so every post already in the table
would stay mislabelled and the French view would stay empty. This migration
re-scores the stored rows with app.services.lang.detect_lang.

It also appends French clinical vocabulary to the configured social keywords so
searches are issued in the terms French clinicians actually use (CBNPC, not
NSCLC). Existing keywords are preserved — this is a union, not a replacement.

Cost note: only the Instagram job bills Apify per keyword (Twitter and LinkedIn
route through TinyFish, which is free), so the appended terms add one Apify
actor run each per scan.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

_CHUNK = 500

# French terms a clinician or patient actually types. Kept deliberately short:
# each one costs an extra Instagram actor run per scan.
_FRENCH_KEYWORDS = [
    "CBNPC",                 # what French oncologists write instead of NSCLC
    "cancerbronchique",
    "oncologiethoracique",
    "depistagepoumon",
    "therapieciblee",
    "soinsdesupport",
    "survieglobale",
    "GustaveRoussy",
    "InstitutCurie",
    "IFCT",                  # Intergroupe Francophone de Cancérologie Thoracique
]


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def _relabel(conn, table: str, text_columns: list[str]) -> int:
    """Re-score `table`.language from its text columns. Returns rows updated."""
    from app.services.lang import detect_lang

    cols = ", ".join(text_columns)
    rows = conn.execute(sa.text(f"SELECT id, {cols} FROM {table}")).fetchall()

    # Group ids by verdict so the write is a handful of bulk UPDATEs rather
    # than one statement per row.
    by_lang: dict[str, list[int]] = {}
    for row in rows:
        text = " ".join(str(v) for v in row[1:] if v)
        by_lang.setdefault(detect_lang(text), []).append(row[0])

    stmt = sa.text(
        f"UPDATE {table} SET language = :lang WHERE id IN :ids"
    ).bindparams(sa.bindparam("ids", expanding=True))

    updated = 0
    for lang, ids in by_lang.items():
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            conn.execute(stmt, {"lang": lang, "ids": chunk})
            updated += len(chunk)
    return updated


def upgrade():
    conn = op.get_bind()

    if _has_table("social_posts"):
        _relabel(conn, "social_posts", ["text"])
    if _has_table("discovery_results"):
        _relabel(conn, "discovery_results", ["title", "snippet"])

    # Append French clinical vocabulary to the configured keyword list.
    if _has_table("app_settings"):
        row = conn.execute(
            sa.text("SELECT social_keywords FROM app_settings WHERE id = 1")
        ).fetchone()
        if row is not None:
            try:
                current = json.loads(row[0]) if row[0] else []
            except (TypeError, ValueError):
                current = []
            if not isinstance(current, list):
                current = []
            # Case-insensitive union, original order preserved.
            seen = {str(k).strip().lower() for k in current}
            merged = list(current) + [
                k for k in _FRENCH_KEYWORDS if k.lower() not in seen
            ]
            if len(merged) != len(current):
                conn.execute(
                    sa.text("UPDATE app_settings SET social_keywords = :kw WHERE id = 1"),
                    {"kw": json.dumps(merged)},
                )


def downgrade():
    """Language labels are derived data — there is no prior value to restore, and
    re-running the old broken heuristic would be worse than leaving them. The
    appended keywords are removed so the setting returns to its former list."""
    conn = op.get_bind()
    if not _has_table("app_settings"):
        return
    row = conn.execute(
        sa.text("SELECT social_keywords FROM app_settings WHERE id = 1")
    ).fetchone()
    if row is None:
        return
    try:
        current = json.loads(row[0]) if row[0] else []
    except (TypeError, ValueError):
        return
    if not isinstance(current, list):
        return
    added = {k.lower() for k in _FRENCH_KEYWORDS}
    kept = [k for k in current if str(k).strip().lower() not in added]
    conn.execute(
        sa.text("UPDATE app_settings SET social_keywords = :kw WHERE id = 1"),
        {"kw": json.dumps(kept)},
    )
