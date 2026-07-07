"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("known_urls", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_targets_name", "targets", ["name"])
    op.create_index("ix_targets_active", "targets", ["active"])

    op.create_table(
        "scraped_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(64)),
        sa.Column("source_name", sa.String(255)),
        sa.Column("title", sa.Text()),
        sa.Column("raw_content", sa.Text()),
        sa.Column("published_date", sa.String(32)),
        sa.Column("content_hash", sa.String(64), unique=True),
        sa.Column("chroma_id", sa.String(128)),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("idempotency_key", sa.String(128), unique=True),
    )
    op.create_index("ix_scraped_posts_target_id", "scraped_posts", ["target_id"])
    op.create_index("ix_scraped_posts_content_hash", "scraped_posts", ["content_hash"])
    op.create_index("ix_scraped_posts_target_scraped", "scraped_posts", ["target_id", "scraped_at"])

    op.create_table(
        "extracted_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scraped_post_id", sa.Integer(), sa.ForeignKey("scraped_posts.id"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("topic", sa.String(512)),
        sa.Column("context", sa.Text()),
        sa.Column("what_they_said", sa.Text()),
        sa.Column("sentiment", sa.String(32)),
        sa.Column("category", sa.String(64)),
        sa.Column("window_tag", sa.String(32), server_default="primary"),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_extracted_insights_target_id", "extracted_insights", ["target_id"])
    op.create_index("ix_extracted_insights_post_id", "extracted_insights", ["scraped_post_id"])

    op.create_table(
        "run_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("celery_task_id", sa.String(128), unique=True),
        sa.Column("idempotency_key", sa.String(128), unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("total_targets", sa.Integer(), server_default="0"),
        sa.Column("targets_processed", sa.Integer(), server_default="0"),
        sa.Column("new_posts_found", sa.Integer(), server_default="0"),
        sa.Column("duplicates_skipped", sa.Integer(), server_default="0"),
        sa.Column("insights_extracted", sa.Integer(), server_default="0"),
        sa.Column("pdfs_generated", sa.Integer(), server_default="0"),
        sa.Column("current_target", sa.String(255)),
        sa.Column("error_message", sa.Text()),
        sa.Column("llm_calls_used", sa.Integer(), server_default="0"),
    )
    op.create_index("ix_run_logs_status", "run_logs", ["status"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("llm_provider", sa.String(64), server_default="vertex"),
        sa.Column("llm_pro_model", sa.String(128), server_default="gemini-2.5-pro"),
        sa.Column("llm_flash_model", sa.String(128), server_default="gemini-2.5-flash"),
        sa.Column("cron_hour", sa.Integer(), server_default="8"),
        sa.Column("cron_minute", sa.Integer(), server_default="0"),
        sa.Column("cron_enabled", sa.Boolean(), server_default="true"),
        sa.Column("agent_budget_per_run", sa.Integer(), server_default="250"),
        sa.Column("llm_budget_hard_stop", sa.Integer(), server_default="500"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "person_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("run_logs.id")),
        sa.Column("summary_bullets", sa.Text()),
        sa.Column("so_what_pharma", sa.Text()),
        sa.Column("insights_count", sa.Integer(), server_default="0"),
        sa.Column("summary_bullets_extended", sa.Text()),
        sa.Column("so_what_pharma_extended", sa.Text()),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_person_summaries_target_id", "person_summaries", ["target_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("agent_messages")
    op.drop_table("person_summaries")
    op.drop_table("app_settings")
    op.drop_table("run_logs")
    op.drop_table("extracted_insights")
    op.drop_table("scraped_posts")
    op.drop_table("targets")
