"""Transparence Santé: industry payments to French healthcare professionals.

France's Sunshine Act register (10.1M declarations, free public API) gives the
platform two things scraped content cannot: which competitor is paying which
French KOL and how much — the competitive-intelligence section the client left
blank — and an external measure of who the field treats as influential.

Two design points are load-bearing enough to record here, because they are the
difference between this feature being trustworthy and being a liability.

**Identity is pinned to RPPS, never matched on a name.** The register is filed
by thousands of companies with no shared convention. Prof. Barlési appears under
six different city spellings (VILLEJUIF CEDEX / Villejuif / VILLEJUIF /
MARSEILLE / MARSEILLE CEDEX 05 / MARSEILLE--5E--ARRONDI) because he changed
institution and because formatting is inconsistent, so city both fragments one
person and legitimately changes. Meanwhile "MARTIN" covers many unrelated
physicians. What is stable is the national RPPS identifier: 7.7M of the 10.1M
records carry one, and 224 of Barlési's 269 records share RPPS 10003416467.
`targets.transparence_rpps` holds that pin; every payment query filters on it.

**An unconfident match shows nothing.** `transparence_status` gates display.
Only "resolved" renders figures; "ambiguous" and "not_found" render an
explanation. A payment attributed to the wrong clinician in a client-facing
competitive brief is worse than a missing one, because the reader cannot tell it
is wrong. The same silent-wrong-person trap already bit the OpenAlex and
ClinicalTrials.gov author lanes.

`declaration_id` is unique so re-syncing an overlapping window is idempotent —
the same natural-key dedup `scraped_posts.content_hash` uses.
"""
from alembic import op
import sqlalchemy as sa

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transparence_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("declaration_id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.Integer(),
                  sa.ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rpps", sa.String(32), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("company_siren", sa.String(32), nullable=True),
        sa.Column("amount_eur", sa.Float(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transparence_payments_declaration_id", "transparence_payments",
                    ["declaration_id"], unique=True)
    op.create_index("ix_transparence_payments_target_id", "transparence_payments", ["target_id"])
    op.create_index("ix_transparence_payments_rpps", "transparence_payments", ["rpps"])
    op.create_index("ix_transparence_payments_company", "transparence_payments", ["company"])
    op.create_index("ix_transparence_payments_company_siren", "transparence_payments",
                    ["company_siren"])
    op.create_index("ix_transparence_payments_paid_on", "transparence_payments", ["paid_on"])
    op.create_index("ix_transparence_payments_kind", "transparence_payments", ["kind"])
    # The incremental sync cursor: only pull what was published since last time.
    op.create_index("ix_transparence_payments_published_on", "transparence_payments",
                    ["published_on"])

    op.add_column("targets", sa.Column("transparence_rpps", sa.String(32), nullable=True))
    op.add_column("targets", sa.Column("transparence_status", sa.String(16),
                                       nullable=False, server_default="unresolved"))
    op.add_column("targets", sa.Column("transparence_confidence", sa.Float(), nullable=True))
    op.add_column("targets", sa.Column("transparence_note", sa.String(255), nullable=True))
    op.add_column("targets", sa.Column("transparence_resolved_at",
                                       sa.DateTime(timezone=True), nullable=True))
    op.add_column("targets", sa.Column("transparence_synced_at",
                                       sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_targets_transparence_rpps", "targets", ["transparence_rpps"])
    op.create_index("ix_targets_transparence_status", "targets", ["transparence_status"])


def downgrade() -> None:
    op.drop_index("ix_targets_transparence_status", table_name="targets")
    op.drop_index("ix_targets_transparence_rpps", table_name="targets")
    for col in ("transparence_synced_at", "transparence_resolved_at", "transparence_note",
                "transparence_confidence", "transparence_status", "transparence_rpps"):
        op.drop_column("targets", col)
    op.drop_table("transparence_payments")
