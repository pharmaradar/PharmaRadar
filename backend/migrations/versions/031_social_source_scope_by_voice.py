"""Recompute social source_scope from the account rather than the domain.

`social_posts.source_scope` was written as `is_french_source(post_url)` — a
domain test. Every social post lives on a global platform domain, so the only
rows that could ever score "fr" were the ones from fr.linkedin.com. Measured on
the live table before this ran: 54 of 299 rows were "fr", and 121 French-language
posts from French institutions — including @SPLF_SocPneumo, a French learned
society in our own curated registry — sat in "global" purely because they post
on x.com.

The read filters now select French content by source_scope, so those rows have
to be relabelled or the France view would lose them. Scope is recomputed here
with the same `french_voice` rule the ingest paths now use, so stored rows and
new rows mean the same thing.

Revision ID: 031
Revises: 030
"""
from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, post_url, author, language FROM social_posts"
    )).fetchall()

    # Imported inside the migration: the rule lives in the service layer, and
    # duplicating it here would let the two drift apart silently.
    from app.services.fr_sources import Scope, french_voice

    tracked = tuple(
        r[0] for r in conn.execute(sa.text(
            "SELECT handle FROM tracked_accounts WHERE active = true"
        )).fetchall()
    )

    fr_ids, global_ids = [], []
    for row_id, url, author, language in rows:
        target = (fr_ids if french_voice(url or "", author or "", language, tracked)
                  else global_ids)
        target.append(row_id)

    # Chunked: a single ANY(...) with tens of thousands of ids is a large
    # parameter payload, and this table only grows.
    for ids, scope in ((fr_ids, Scope.FR.value), (global_ids, Scope.GLOBAL.value)):
        for i in range(0, len(ids), 500):
            conn.execute(
                sa.text("UPDATE social_posts SET source_scope = :scope "
                        "WHERE id = ANY(:ids)"),
                {"scope": scope, "ids": ids[i:i + 500]},
            )


def downgrade() -> None:
    # Restore the domain-derived labelling this replaced.
    op.execute("""
        UPDATE social_posts
           SET source_scope = CASE
               WHEN domain ILIKE 'fr.%' OR domain ILIKE '%.fr' THEN 'fr'
               ELSE 'global'
           END
    """)
