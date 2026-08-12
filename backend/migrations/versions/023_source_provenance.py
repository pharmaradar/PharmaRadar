"""Source-level France targeting — provenance columns + per-target scope

Revision ID: 023
Revises: 022
Create Date: 2026-08-11

The client's requirement is a SOURCE requirement, not a language one: content must
come from French publications, institutions and accounts. That makes "which source
did this come from" a fact worth storing rather than re-deriving from text.

- scraped_posts.domain / source_scope / source_category — written at save time by
  services/scraper.py from the URL. Before this, `source_type` was never assigned
  anywhere in the codebase (only read), so the share of content coming from French
  sources was not measurable at all.
- social_posts.domain / source_scope — same, for the social lane.
- targets.source_scope — per-target acquisition scope, defaulting to 'fr'.

Existing rows are backfilled from their stored URLs, so the France share is
correct for historical content immediately rather than only for new scrapes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

_CHUNK = 500


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def _add(table: str, column: sa.Column, index: str | None = None) -> None:
    if _has_table(table) and not _has_column(table, column.name):
        op.add_column(table, column)
        if index:
            op.create_index(index, table, [column.name])


def _backfill(conn, table: str, url_column: str, with_category: bool) -> int:
    """Derive provenance for existing rows from their stored URL."""
    from app.services.fr_sources import (
        Scope,
        is_french_source,
        normalize_host,
        source_category,
    )

    rows = conn.execute(
        sa.text(f"SELECT id, {url_column} FROM {table} WHERE {url_column} IS NOT NULL")
    ).fetchall()

    # Group by identical provenance so this is a handful of bulk UPDATEs rather
    # than one statement per row.
    buckets: dict[tuple, list[int]] = {}
    for row_id, url in rows:
        key = (
            normalize_host(url),
            Scope.FR.value if is_french_source(url) else Scope.GLOBAL.value,
            source_category(url) if with_category else None,
        )
        buckets.setdefault(key, []).append(row_id)

    if with_category:
        stmt = sa.text(
            f"UPDATE {table} SET domain = :domain, source_scope = :scope, "
            f"source_category = :category WHERE id IN :ids"
        )
    else:
        stmt = sa.text(
            f"UPDATE {table} SET domain = :domain, source_scope = :scope WHERE id IN :ids"
        )
    stmt = stmt.bindparams(sa.bindparam("ids", expanding=True))

    updated = 0
    for (domain, scope, category), ids in buckets.items():
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            params = {"domain": domain or None, "scope": scope, "ids": chunk}
            if with_category:
                params["category"] = category
            conn.execute(stmt, params)
            updated += len(chunk)
    return updated


def upgrade():
    _add("scraped_posts", sa.Column("domain", sa.String(255), nullable=True),
         "ix_scraped_posts_domain")
    _add("scraped_posts", sa.Column("source_scope", sa.String(8), nullable=True),
         "ix_scraped_posts_source_scope")
    _add("scraped_posts", sa.Column("source_category", sa.String(32), nullable=True))

    _add("social_posts", sa.Column("domain", sa.String(255), nullable=True),
         "ix_social_posts_domain")
    _add("social_posts", sa.Column("source_scope", sa.String(8), nullable=True),
         "ix_social_posts_source_scope")

    _add("discovery_results", sa.Column("domain", sa.String(255), nullable=True),
         "ix_discovery_results_domain")
    _add("discovery_results", sa.Column("source_scope", sa.String(8), nullable=True),
         "ix_discovery_results_source_scope")

    # Per-target acquisition scope. NOT NULL with a server default so existing
    # rows adopt the French default without a separate backfill pass.
    if _has_table("targets") and not _has_column("targets", "source_scope"):
        op.add_column(
            "targets",
            sa.Column("source_scope", sa.String(8), nullable=False, server_default="fr"),
        )

    conn = op.get_bind()
    if _has_table("scraped_posts"):
        _backfill(conn, "scraped_posts", "source_url", with_category=True)
    if _has_table("social_posts"):
        _backfill(conn, "social_posts", "post_url", with_category=False)
    if _has_table("discovery_results"):
        _backfill(conn, "discovery_results", "url", with_category=False)


def downgrade():
    for table, columns, indexes in (
        ("scraped_posts", ["source_category", "source_scope", "domain"],
         ["ix_scraped_posts_source_scope", "ix_scraped_posts_domain"]),
        ("social_posts", ["source_scope", "domain"],
         ["ix_social_posts_source_scope", "ix_social_posts_domain"]),
        ("discovery_results", ["source_scope", "domain"],
         ["ix_discovery_results_source_scope", "ix_discovery_results_domain"]),
        ("targets", ["source_scope"], []),
    ):
        if not _has_table(table):
            continue
        for index in indexes:
            try:
                op.drop_index(index, table_name=table)
            except Exception:
                pass
        for column in columns:
            if _has_column(table, column):
                op.drop_column(table, column)
