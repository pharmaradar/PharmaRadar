"""social_posts table + social scan config on app_settings

Revision ID: 009
Revises: 008
Create Date: 2026-05-28

Idempotent: guards every DDL op against existing objects so it reconciles
cleanly whether or not SQLAlchemy's create_all already materialized the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_table(insp, "social_posts"):
        op.create_table(
            "social_posts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("platform", sa.String(32), nullable=False, index=True),
            sa.Column("post_url", sa.Text(), nullable=False),
            sa.Column("author", sa.String(255), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("thumbnail_url", sa.Text(), nullable=True),
            sa.Column("likes", sa.Integer(), server_default="0"),
            sa.Column("comments", sa.Integer(), server_default="0"),
            sa.Column("views", sa.Integer(), server_default="0"),
            sa.Column("shares", sa.Integer(), server_default="0"),
            sa.Column("hashtags", sa.Text(), nullable=True),
            sa.Column("query", sa.String(255), nullable=True, index=True),
            sa.Column("kind", sa.String(16), server_default="field"),
            sa.Column("disease_area", sa.String(64), nullable=True, index=True),
            sa.Column("topic", sa.String(255), nullable=True),
            sa.Column("llm_description", sa.Text(), nullable=True),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True, index=True),
            sa.Column("content_hash", sa.String(64), nullable=True, unique=True, index=True),
            sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    cols = {
        "social_keywords": sa.Column("social_keywords", sa.Text(), nullable=True),
        "social_platforms": sa.Column("social_platforms", sa.String(256),
                                       server_default='["instagram","twitter","linkedin","facebook"]'),
        "social_window_days": sa.Column("social_window_days", sa.Integer(), server_default="180"),
        "social_max_per_query": sa.Column("social_max_per_query", sa.Integer(), server_default="30"),
        "social_scan_enabled": sa.Column("social_scan_enabled", sa.Boolean(), server_default=sa.false()),
        "social_scan_frequency": sa.Column("social_scan_frequency", sa.String(16), server_default="weekly"),
        "social_scan_hour": sa.Column("social_scan_hour", sa.Integer(), server_default="6"),
        "social_include_kols": sa.Column("social_include_kols", sa.Boolean(), server_default=sa.true()),
    }
    for name, col in cols.items():
        if not _has_column(insp, "app_settings", name):
            op.add_column("app_settings", col)


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    for col in ("social_include_kols", "social_scan_hour", "social_scan_frequency",
                "social_scan_enabled", "social_max_per_query", "social_window_days",
                "social_platforms", "social_keywords"):
        if _has_column(insp, "app_settings", col):
            op.drop_column("app_settings", col)
    if _has_table(insp, "social_posts"):
        op.drop_table("social_posts")
