"""Add multi-provider fields to app_settings

Revision ID: 002
Revises: 001
Create Date: 2026-05-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("api_key", sa.String(512), nullable=True))
    op.add_column("app_settings", sa.Column("ollama_base_url", sa.String(256),
                  server_default="http://localhost:11434", nullable=False))
    op.add_column("app_settings", sa.Column("nvidia_base_url", sa.String(256),
                  server_default="https://integrate.api.nvidia.com/v1", nullable=False))
    op.add_column("app_settings", sa.Column("custom_base_url", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "custom_base_url")
    op.drop_column("app_settings", "nvidia_base_url")
    op.drop_column("app_settings", "ollama_base_url")
    op.drop_column("app_settings", "api_key")
