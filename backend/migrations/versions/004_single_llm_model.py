"""Replace llm_pro_model + llm_flash_model with single llm_model

Revision ID: 004
Revises: 003
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("llm_model", sa.String(128), nullable=True))
    # Copy flash model value into new column, fall back to default
    op.execute(
        "UPDATE app_settings SET llm_model = COALESCE(llm_flash_model, 'gemini-2.5-flash')"
    )
    op.alter_column("app_settings", "llm_model", nullable=False, server_default="gemini-2.5-flash")
    op.drop_column("app_settings", "llm_pro_model")
    op.drop_column("app_settings", "llm_flash_model")


def downgrade():
    op.add_column("app_settings", sa.Column("llm_pro_model", sa.String(128), nullable=False, server_default="gemini-2.5-pro"))
    op.add_column("app_settings", sa.Column("llm_flash_model", sa.String(128), nullable=False, server_default="gemini-2.5-flash"))
    op.execute("UPDATE app_settings SET llm_flash_model = llm_model")
    op.drop_column("app_settings", "llm_model")
