"""Resolve the Facebook accounts migration 039 left unconfirmed.

039 corrected five wrong slugs and explicitly left six unconfirmed rather than
guess: `ansm.sante.fr, RocheFrance, Cancer.Info.Service, RespirEspoir,
esmo.oncology, ASCO.org`. Checked each against live data since:

  ASCO.org        -> resolves to `a.s.coorg`, a private individual's profile,
                     not the American Society of Clinical Oncology. Corrected
                     to ASCOCancer (verified: ASCO's own page, ~330k likes,
                     "World's leading professional organization for oncology
                     professionals caring for people with cancer").
  esmo.oncology   -> dead slug, `error: private or deleted`. Corrected to
                     esmo.org (verified: ESMO's own page, 41,890 followers).
  RocheFrance     -> resolves to "Roche Musique", a French nightlife/event
                     promotion page run by someone named Alex Roche — nothing
                     to do with the pharmaceutical company. No correct handle
                     found, so removed rather than left pointing at the wrong
                     organisation's content.
  LillyOncology   -> dead slug. Eli Lilly has no dedicated Oncology page —
                     only the main corporate page and an unrelated "Lilly
                     Trials" page. This handle appears to never have existed.
                     Removed.
  Cancer.Info.Service -> dead slug. "Cancer info service" is INCa's phone/web
                     helpline (0805 123 124), not a page with its own Facebook
                     presence. `laliguecontrelecancer` already tracks the real
                     organisation behind half of that service. Removed.

`ansm.sante.fr` and `RespirEspoir` are left alone: their handles are correct,
but a separate bug (fixed alongside this migration, see
`app.tasks.accounts._scan_one`) had been crediting them with an unrelated
landing-page row from a keyword search as if it were a real post. That is a
code fix, not a data one — no schema change needed for it.

Revision ID: 047
Revises: 046
"""
from alembic import op
import sqlalchemy as sa

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None

# (old handle, new handle, label)
_CORRECTIONS = (
    ("ASCO.org", "ASCOCancer", "ASCO"),
    ("esmo.oncology", "esmo.org", "ESMO"),
)

# Rows removed outright: wrong page with no confirmed replacement, or a dead
# slug with no real page behind it at all.
_REMOVE = (
    ("facebook", "RocheFrance",
     "https://www.facebook.com/RocheFrance", "Rochefrance"),
    ("facebook", "LillyOncology",
     "https://www.facebook.com/LillyOncology", "Lillyoncology"),
    ("facebook", "Cancer.Info.Service",
     "https://www.facebook.com/Cancer.Info.Service", "Cancer Info Service"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for old, new, label in _CORRECTIONS:
        # Same guard as 039: the UNIQUE (platform, handle) makes a collision
        # fatal mid-deploy, and skipping is safer than failing the migration.
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
                              last_scanned_at = NULL,
                              last_scan_status = NULL,
                              post_count = 0
                        WHERE platform = 'facebook' AND lower(handle) = lower(:old)"""),
            {"new": new, "label": label, "old": old,
             "url": f"https://www.facebook.com/{new}"},
        )

    for platform, handle, _url, _label in _REMOVE:
        # ON DELETE SET NULL on social_posts.tracked_account_id — matches what
        # the Account Tracking page's own delete button does. None of these
        # three have any linked posts, so nothing to detach in practice.
        conn.execute(
            sa.text("""DELETE FROM tracked_accounts
                        WHERE platform = :p AND lower(handle) = lower(:h)"""),
            {"p": platform, "h": handle},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new, _label in _CORRECTIONS:
        conn.execute(
            sa.text("""UPDATE tracked_accounts
                          SET handle = :old, url = :url,
                              last_scanned_at = NULL, last_scan_status = NULL
                        WHERE platform = 'facebook' AND lower(handle) = lower(:new)"""),
            {"old": old, "new": new, "url": f"https://www.facebook.com/{old}"},
        )

    # Restored on their old, wrong slug deliberately — downgrade recreates the
    # pre-migration state, not a fixed one.
    for platform, handle, url, label in _REMOVE:
        conn.execute(
            sa.text("""INSERT INTO tracked_accounts
                         (platform, handle, url, label, active, post_count, created_at)
                       VALUES (:p, :h, :u, :l, false, 0, now())
                       ON CONFLICT (platform, handle) DO NOTHING"""),
            {"p": platform, "h": handle, "u": url, "l": label},
        )
