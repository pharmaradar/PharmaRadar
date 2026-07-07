"""Add weekly schedule fields to app_settings

Revision ID: 003
Revises: 002
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("cron_frequency", sa.String(16), nullable=False, server_default="weekly"))
    op.add_column("app_settings", sa.Column("cron_day_of_week", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("app_settings", "cron_day_of_week")
    op.drop_column("app_settings", "cron_frequency")
