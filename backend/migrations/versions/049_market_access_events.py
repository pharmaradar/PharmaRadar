"""French market-access events: HAS added-benefit rulings and ANSM shortages.

Two signals a French medical-affairs team treats as commercial events and that
scraped KOL content reports late, second-hand, or not at all.

ASMR is the Commission de la Transparence's rating of a drug's added clinical
benefit (I major … V none); it drives price and reimbursement, so a competitor's
rating is market intelligence. Measured on the client's own tracked portfolio:
163 rulings on file — Roche 57, BMS 40, MSD 32, AstraZeneca 24 — including
Imfinzi taking ASMR IV in Feb 2026 on a phase III superiority result and Columvi
taking ASMR V in Mar 2026 on studies that "ne permettent pas" the claim.

ANSM shortages are the supply side: a competitor unable to supply is an
opportunity, our own shortage is a risk. Currently zero tracked drugs are short,
which is an honest zero — the parser reads all 640 live tensions and none of
them is an oncology product we follow.

Only drugs matching services/brands.py are stored. The register covers 15,857
specialities across all of medicine, and keeping all of it would bury the signal
under dermatology and vaccines; reusing the brand registry means "a drug we
track" has one definition here and in share-of-voice.

`content_hash` rather than a CIS key, because the source files are full daily
snapshots: one drug accumulates many rulings, one ruling covers many
presentations, and the same file is re-read every night.
"""
from alembic import op
import sqlalchemy as sa

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_access_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("cis_code", sa.String(16), nullable=False),
        sa.Column("drug_name", sa.String(512), nullable=False),
        sa.Column("brand", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("holder", sa.String(255), nullable=True),
        sa.Column("rating", sa.String(64), nullable=True),
        sa.Column("opinion_ref", sa.String(32), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_market_access_events_content_hash", "market_access_events",
                    ["content_hash"], unique=True)
    op.create_index("ix_market_access_events_kind", "market_access_events", ["kind"])
    op.create_index("ix_market_access_events_cis_code", "market_access_events", ["cis_code"])
    op.create_index("ix_market_access_events_brand", "market_access_events", ["brand"])
    op.create_index("ix_market_access_events_owner", "market_access_events", ["owner"])
    op.create_index("ix_market_access_events_rating", "market_access_events", ["rating"])
    op.create_index("ix_market_access_events_event_date", "market_access_events", ["event_date"])
    op.create_index("ix_market_access_brand_date", "market_access_events",
                    ["brand", "event_date"])
    op.create_index("ix_market_access_owner_date", "market_access_events",
                    ["owner", "event_date"])


def downgrade() -> None:
    op.drop_table("market_access_events")
