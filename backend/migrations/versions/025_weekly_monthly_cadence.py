"""Retire the daily run cadence; add monthly

Revision ID: 025
Revises: 024
Create Date: 2026-08-12

The client asked for the report schedule to offer Weekly or Monthly only. A
report covering a 30-day window has little new to say every 24 hours, and each
run spends real scraping credit.

Existing rows set to 'daily' are rewritten to 'weekly' — the closest surviving
cadence, and the cheaper direction to fail in. Without this rewrite the UI would
show neither option selected while the scheduler kept firing every day.

`cron_day_of_month` is capped at 28 by the API so every month contains it;
scheduling on the 31st would silently skip most of the year.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_table("app_settings"):
        return

    if not _has_column("app_settings", "cron_day_of_month"):
        op.add_column(
            "app_settings",
            sa.Column("cron_day_of_month", sa.Integer(), nullable=False, server_default="1"),
        )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE app_settings SET cron_frequency = 'weekly' "
            "WHERE cron_frequency IS NULL OR lower(cron_frequency) NOT IN ('weekly', 'monthly')"
        )
    )


def downgrade():
    """Only the column is removed. Rows that were 'daily' before the upgrade are
    not restorable — the original value was overwritten in place — and leaving
    them weekly is harmless."""
    if _has_table("app_settings") and _has_column("app_settings", "cron_day_of_month"):
        op.drop_column("app_settings", "cron_day_of_month")
