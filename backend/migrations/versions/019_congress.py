"""Congress tracking and congress-owned reports.

Revision ID: 019
Revises: 018
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def _inspector() -> Inspector:
    return Inspector.from_engine(op.get_bind())


def _has_table(name: str) -> bool:
    try:
        return name in _inspector().get_table_names()
    except Exception:
        return False


def _has_column(table: str, column: str) -> bool:
    try:
        return any(c["name"] == column for c in _inspector().get_columns(table))
    except Exception:
        return False


def _has_check(table: str, name: str) -> bool:
    try:
        return any(c.get("name") == name for c in _inspector().get_check_constraints(table))
    except Exception:
        return False


def upgrade():
    if not _has_table("congresses"):
        op.create_table(
            "congresses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("hashtags", sa.Text(), nullable=True),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("disease_area", sa.String(128), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_table("congress_questions"):
        op.create_table(
            "congress_questions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "congress_id", sa.Integer(),
                sa.ForeignKey("congresses.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("question_text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_column("burning_topic_reports", "congress_id"):
        op.alter_column("burning_topic_reports", "topic_id", nullable=True)
        op.add_column(
            "burning_topic_reports",
            sa.Column("congress_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_burning_topic_reports_congress_id",
            "burning_topic_reports", "congresses",
            ["congress_id"], ["id"], ondelete="CASCADE",
        )

    if not _has_column("burning_topic_reports", "question_answers"):
        op.add_column(
            "burning_topic_reports",
            sa.Column("question_answers", sa.Text(), nullable=True),
        )

    if not _has_check("burning_topic_reports", "ck_burning_topic_reports_one_owner"):
        op.create_check_constraint(
            "ck_burning_topic_reports_one_owner",
            "burning_topic_reports",
            "(topic_id IS NOT NULL AND congress_id IS NULL) OR "
            "(topic_id IS NULL AND congress_id IS NOT NULL)",
        )


def downgrade():
    if _has_check("burning_topic_reports", "ck_burning_topic_reports_one_owner"):
        op.drop_constraint(
            "ck_burning_topic_reports_one_owner", "burning_topic_reports", type_="check"
        )
    if _has_column("burning_topic_reports", "question_answers"):
        op.drop_column("burning_topic_reports", "question_answers")
    if _has_column("burning_topic_reports", "congress_id"):
        op.drop_constraint(
            "fk_burning_topic_reports_congress_id", "burning_topic_reports", type_="foreignkey"
        )
        # Congress-owned reports have topic_id NULL — they cannot survive the
        # NOT NULL restore below (their owning table is being dropped anyway).
        # Without this DELETE the downgrade hard-fails once any congress report
        # exists (NotNullViolationError).
        op.execute("DELETE FROM burning_topic_reports WHERE topic_id IS NULL")
        op.drop_column("burning_topic_reports", "congress_id")
        op.alter_column("burning_topic_reports", "topic_id", nullable=False)
    if _has_table("congress_questions"):
        op.drop_table("congress_questions")
    if _has_table("congresses"):
        op.drop_table("congresses")
