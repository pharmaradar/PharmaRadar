"""Auth: users + per-user search_history + agent_messages.user_id

Revision ID: 014
Revises: 013
Create Date: 2026-05-29

Idempotent — guarded by inspector checks so it's safe alongside create_all.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    return name in insp.get_table_names()


def _has_col(table: str, col: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return col in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("hashed_password", sa.String(255), nullable=False),
            sa.Column("role", sa.String(16), nullable=False, server_default="user"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if not _has_table("search_history"):
        op.create_table(
            "search_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("query", sa.String(512), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_search_history_user_id", "search_history", ["user_id"])

    if not _has_col("agent_messages", "user_id"):
        op.add_column("agent_messages", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_index("ix_agent_messages_user_id", "agent_messages", ["user_id"])


def downgrade():
    if _has_col("agent_messages", "user_id"):
        op.drop_index("ix_agent_messages_user_id", "agent_messages")
        op.drop_column("agent_messages", "user_id")
    if _has_table("search_history"):
        op.drop_table("search_history")
    if _has_table("users"):
        op.drop_table("users")
