"""Tracked social accounts registry

Revision ID: 028
Revises: 027
Create Date: 2026-08-12

The client asked to "define and track specific social media accounts". Until now
the French accounts were a hardcoded tuple in services/fr_sources and Facebook
pages a JSON blob in AppSettings, so the team could not add one.

Seeded from both of those so nothing that was being monitored stops being
monitored. Every X handle in the seed was verified by probing
`site:x.com/<handle>` and confirming the results are that account's own posts —
a researched list invents handles, a probe proves them.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None

# (handle, label, category) — verified live on 2026-08-12.
_X_SEED = [
    ("GustaveRoussy", "Gustave Roussy", "cancer_centre"),
    ("institut_curie", "Institut Curie", "cancer_centre"),
    ("Inserm", "Inserm", "institution"),
    ("Institut_cancer", "INCa", "institution"),
    ("Unicancer", "Unicancer", "institution"),
    ("HAS_sante", "Haute Autorité de Santé", "institution"),
    ("ansm", "ANSM", "institution"),
    ("laliguecancer", "Ligue contre le cancer", "patient_association"),
    ("FondationARC", "Fondation ARC", "patient_association"),
    ("Roche_France", "Roche France", "pharma"),
    ("SanofiFR", "Sanofi France", "pharma"),
    ("BMSFrance", "BMS France", "pharma"),
    ("leQdM", "Le Quotidien du Médecin", "medical_press"),
    ("univadisfr", "Univadis France", "medical_press"),
]


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    if not _has_table("tracked_accounts"):
        op.create_table(
            "tracked_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("handle", sa.String(128), nullable=False),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("label", sa.String(255), nullable=True),
            sa.Column("category", sa.String(32), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("platform", "handle",
                                name="uq_tracked_accounts_platform_handle"),
        )
        op.create_index("ix_tracked_accounts_platform", "tracked_accounts", ["platform"])
        op.create_index("ix_tracked_accounts_active", "tracked_accounts", ["active"])

    conn = op.get_bind()
    insert = sa.text(
        "INSERT INTO tracked_accounts (platform, handle, url, label, category, active) "
        "VALUES (:platform, :handle, :url, :label, :category, true) "
        "ON CONFLICT (platform, handle) DO NOTHING"
    )
    for handle, label, category in _X_SEED:
        conn.execute(insert, {
            "platform": "twitter", "handle": handle,
            "url": f"https://x.com/{handle}", "label": label, "category": category,
        })

    # Carry across whatever Facebook pages were already configured, so enabling
    # the registry never silently narrows what is being watched.
    if _has_table("app_settings"):
        import json
        row = conn.execute(
            sa.text("SELECT facebook_page_urls FROM app_settings WHERE id = 1")
        ).fetchone()
        try:
            pages = json.loads(row[0]) if row and row[0] else []
        except (TypeError, ValueError):
            pages = []
        for url in pages if isinstance(pages, list) else []:
            if not isinstance(url, str) or not url.strip():
                continue
            slug = url.rstrip("/").split("/")[-1]
            conn.execute(insert, {
                "platform": "facebook", "handle": slug, "url": url.strip(),
                "label": slug.replace(".", " ").replace("-", " ").title(), "category": None,
            })


def downgrade():
    if _has_table("tracked_accounts"):
        op.drop_index("ix_tracked_accounts_active", table_name="tracked_accounts")
        op.drop_index("ix_tracked_accounts_platform", table_name="tracked_accounts")
        op.drop_table("tracked_accounts")
