"""Ad-hoc market-research reports (Topic Explorer)

Revision ID: 026
Revises: 025
Create Date: 2026-08-12

Topic Explorer becomes a place to ask a question and get a structured
market-research report. Those reports need to survive a Redis flush — they cost
an LLM call each — so they get a table rather than cache.

Separate from `burning_topic_reports` on purpose: that table has a CHECK
constraint admitting exactly one owner (topic_id XOR congress_id), and relaxing
it to accept a free-text question would weaken a constraint currently protecting
a working feature. The generator is shared; only the storage differs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    if _has_table("market_reports"):
        return

    op.create_table(
        "market_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("language", sa.String(8), nullable=True, server_default="fr"),
        # The six required sections.
        sa.Column("exec_summary", sa.Text(), nullable=True),
        sa.Column("so_what", sa.Text(), nullable=True),
        sa.Column("what_is_said", sa.Text(), nullable=True),
        sa.Column("voices_note", sa.Text(), nullable=True),
        sa.Column("volume_note", sa.Text(), nullable=True),
        sa.Column("subtopics", sa.Text(), nullable=True),
        sa.Column("voice_rows", sa.Text(), nullable=True),
        sa.Column("volume", sa.Text(), nullable=True),
        sa.Column("key_posts", sa.Text(), nullable=True),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("voice_exact_share", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        # SET NULL rather than CASCADE: deleting a user must not destroy reports
        # the team may still be relying on.
        sa.Column("created_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_reports_created_by", "market_reports", ["created_by"])
    op.create_index("ix_market_reports_created_at", "market_reports", ["created_at"])


def downgrade():
    if _has_table("market_reports"):
        op.drop_index("ix_market_reports_created_at", table_name="market_reports")
        op.drop_index("ix_market_reports_created_by", table_name="market_reports")
        op.drop_table("market_reports")
