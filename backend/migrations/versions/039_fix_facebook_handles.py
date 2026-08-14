"""Correct the French Facebook page slugs.

Every French page in the registry returned `not_available` from Facebook — not
because Facebook blocks us (a control run against WHO and Roche returned posts
immediately) but because the slugs were wrong. They had produced nothing since
the day they were added, silently.

The replacements below were found with free web search and then verified with a
single batched Apify run: each returned real posts under its own `pageName`.

  liguecancerfrance             -> laliguecontrelecancer
  unicancer.fr                  -> unicancer
  INCa.Institut.National.Cancer -> Institutnationalducancer
  fondationARC                  -> ARCcancer
  has.sante                     -> Haute.Autorite.de.Sante

Pages left alone: `inserm.fr` already works (8 posts collected), and the rest
(ansm.sante.fr, RocheFrance, Cancer.Info.Service, RespirEspoir, esmo.oncology,
ASCO.org and the global pharma pages) could not be confirmed, so they keep their
current handle and their honest zero rather than being replaced by a guess.

Revision ID: 039
Revises: 038
"""
from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None

# (old handle, verified handle, display label)
_CORRECTIONS = (
    ("liguecancerfrance", "laliguecontrelecancer", "Ligue contre le cancer"),
    ("unicancer.fr", "unicancer", "Unicancer"),
    ("INCa.Institut.National.Cancer", "Institutnationalducancer", "Institut National du Cancer"),
    ("fondationARC", "ARCcancer", "Fondation ARC"),
    ("has.sante", "Haute.Autorite.de.Sante", "Haute Autorité de Santé"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for old, new, label in _CORRECTIONS:
        # Skip if the corrected handle somehow already exists — the table has a
        # UNIQUE (platform, handle) and a failed migration mid-deploy is worse
        # than one page staying on its old slug.
        exists = conn.execute(
            sa.text("""SELECT 1 FROM tracked_accounts
                        WHERE platform = 'facebook' AND lower(handle) = lower(:new)"""),
            {"new": new},
        ).first()
        if exists:
            continue
        conn.execute(
            sa.text("""UPDATE tracked_accounts
                          SET handle = :new,
                              label = :label,
                              url = :url,
                              -- The old slug's scan history describes a page that
                              -- was never reachable, so it is cleared rather than
                              -- carried over onto a different page.
                              last_scanned_at = NULL,
                              last_scan_status = NULL
                        WHERE platform = 'facebook' AND lower(handle) = lower(:old)"""),
            {"new": new, "label": label, "old": old,
             "url": f"https://www.facebook.com/{new}"},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new, _label in _CORRECTIONS:
        conn.execute(
            sa.text("""UPDATE tracked_accounts SET handle = :old, url = NULL
                        WHERE platform = 'facebook' AND lower(handle) = lower(:new)"""),
            {"old": old, "new": new},
        )
