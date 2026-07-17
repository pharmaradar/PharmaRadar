"""Burning Topics — persistent user-defined topic tracker + generated reports

Revision ID: 018
Revises: 017
Create Date: 2026-07-17

Amaury's Burning Topics module: topics live in the DB (name + filters), each
"generate report" run produces a durable burning_topic_reports row with the
synthesis fields + PDF URL. Idempotent — guarded by inspector checks so a
re-run on an already-migrated DB is a no-op. Downgrade drops both tables
cleanly (rollback = `alembic downgrade 017`).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return name in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    if not _has_table("burning_topics"):
        op.create_table(
            "burning_topics",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("language_filter", sa.String(8), nullable=True),
            sa.Column("period_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("exclusion_words", sa.Text(), nullable=True),    # JSON array
            sa.Column("restriction_terms", sa.Text(), nullable=True),  # JSON array
            sa.Column("created_by", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_table("burning_topic_reports"):
        op.create_table(
            "burning_topic_reports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("topic_id", sa.Integer(),
                      sa.ForeignKey("burning_topics.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("summary_md", sa.Text(), nullable=True),
            sa.Column("key_findings", sa.Text(), nullable=True),     # JSON list[str]
            sa.Column("so_what", sa.Text(), nullable=True),
            sa.Column("important_posts", sa.Text(), nullable=True),  # JSON list[{url,title,author,engagement}]
            sa.Column("main_authors", sa.Text(), nullable=True),     # JSON list[{author,posts,engagement,...}]
            sa.Column("pdf_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade():
    if _has_table("burning_topic_reports"):
        op.drop_table("burning_topic_reports")
    if _has_table("burning_topics"):
        op.drop_table("burning_topics")
