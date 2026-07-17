"""Adverse Event classification columns on scraped_posts + social_posts

Revision ID: 020
Revises: 019
Create Date: 2026-07-18

Regulatory requirement (Amaury / Roche pharmacovigilance): AE posts MAY stay
stored, but must NEVER reach a human eye. These columns carry the LLM
classification; every human-visible query filters `is_adverse_event IS NOT
TRUE`. NULL = not yet classified (still visible until classified — filtering
happens on positive classification only, per the agreed spec).

Partial indexes on the NULL rows keep the periodic backfill sweep cheap.
Idempotent via inspector guards; downgrade drops the columns cleanly.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

_TABLES = ("scraped_posts", "social_posts")


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    for table in _TABLES:
        if not _has_column(table, "is_adverse_event"):
            op.add_column(table, sa.Column("is_adverse_event", sa.Boolean(), nullable=True))
            op.add_column(table, sa.Column("ae_reason", sa.Text(), nullable=True))
            op.create_index(
                f"ix_{table}_ae_unclassified", table, ["id"],
                postgresql_where=sa.text("is_adverse_event IS NULL"),
            )


def downgrade():
    for table in _TABLES:
        if _has_column(table, "is_adverse_event"):
            op.drop_index(f"ix_{table}_ae_unclassified", table_name=table)
            op.drop_column(table, "ae_reason")
            op.drop_column(table, "is_adverse_event")
