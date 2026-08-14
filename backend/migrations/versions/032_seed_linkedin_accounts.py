"""Seed the verified French LinkedIn accounts into the registry.

The tracked-accounts table held zero LinkedIn rows, so the new LinkedIn account
lane in `tasks/social._scan_linkedin_accounts` would have had nothing to scrape.

Every slug below was VERIFIED by probe on 2026-08-12, not researched: each was
searched as `<slug> site:fr.linkedin.com/posts` and kept only if at least three
returned URLs had that exact author slug. A probe proves the account exists and
posts; a researched list invents plausible handles that quietly collect nothing.

The probe also corrected four slugs that looked obvious and were wrong —
`gustaveroussy` → `gustave-roussy`, `institut-curie` → `institutcurie`,
`roche-france` → `roche-en-france`, `has-sante` → `haute-autorite-de-sante` —
and rejected `splf`, `centre-leon-berard` and `astrazeneca-france`, which have no
findable posting page under those names.

Deliberately NOT seeded: the bare global `astrazeneca` and `bristol-myers-squibb`
company pages. They exist, but they publish worldwide corporate news, which is
the exact content the client rejected.

Revision ID: 032
Revises: 031
"""
from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

# (handle, label, category) — handle is the LinkedIn slug as it appears in
# fr.linkedin.com/posts/<handle>_...
LINKEDIN_ACCOUNTS = [
    # Learned societies & cooperative groups
    ("ifct", "IFCT — Intergroupe Francophone de Cancérologie Thoracique", "learned_society"),
    ("oncorif", "OncoRIF — réseau oncologie Île-de-France", "learned_society"),
    ("oncopl", "OncoPL — réseau oncologie Pays de la Loire", "learned_society"),
    # Cancer centres & research
    ("gustave-roussy", "Gustave Roussy", "cancer_centre"),
    ("institutcurie", "Institut Curie", "cancer_centre"),
    ("inserm", "Inserm", "cancer_centre"),
    ("unicancer", "Unicancer", "cancer_centre"),
    # Institutions & agencies
    ("institut-national-du-cancer", "INCa — Institut National du Cancer", "institution"),
    ("haute-autorite-de-sante", "HAS — Haute Autorité de Santé", "institution"),
    ("ansm", "ANSM", "institution"),
    # Patient associations
    ("ligue-contre-le-cancer", "Ligue contre le cancer", "patient_association"),
    ("fondation-arc", "Fondation ARC", "patient_association"),
    # Pharma (French affiliates only)
    ("roche-en-france", "Roche en France", "pharma"),
    ("msd-france", "MSD France", "pharma"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for handle, label, category in LINKEDIN_ACCOUNTS:
        # Idempotent and non-clobbering: a row the team already edited keeps its
        # own label, category and active flag.
        conn.execute(
            sa.text("""
                INSERT INTO tracked_accounts (platform, handle, url, label, category, active)
                VALUES ('linkedin', :handle, :url, :label, :category, true)
                ON CONFLICT (platform, handle) DO NOTHING
            """),
            {
                "handle": handle,
                "url": f"https://fr.linkedin.com/company/{handle}",
                "label": label,
                "category": category,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM tracked_accounts "
                "WHERE platform = 'linkedin' AND handle = ANY(:handles)"),
        {"handles": [h for h, _, _ in LINKEDIN_ACCOUNTS]},
    )
