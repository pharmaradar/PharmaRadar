"""Stop spending the social budget on accounts that can never be French.

The client's requirement is French sources only, and their v1 feedback says many
posts arrive in English "despite the active filters". No display filter can fix
that, because the registry was pointed at global corporate pages: `roche`,
`sanofi` and `WHO` produced 6 posts each and **0** that the France view could
use. Every slot they consumed was a slot a French source did not get.

Their French counterparts are already tracked (RocheFrance, Roche_France,
SanofiFR), so this pauses rather than deletes: `active = false` keeps the row and
its history, and the Account Tracking page can switch any of them back on if the
client disagrees.

Accounts with no posts yet are left alone — they are not polluting anything, and
a zero count on the tracking page now means "check the handle", which is a
different conversation.

Also re-backfills `source_scope` where the current detector disagrees with what
was stored. `roche-canada` sat in the France view because it was classified
before `is_francophone_not_france` existed; the detector rejects it correctly
today, so the stored rows just need to catch up.

Revision ID: 036
Revises: 035
"""
from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

# Measured 2026-08-13: 6 posts each, 0 usable in the France view.
_GLOBAL_ACCOUNTS = (
    ("facebook", "roche"),
    ("facebook", "sanofi"),
    ("facebook", "WHO"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for platform, handle in _GLOBAL_ACCOUNTS:
        conn.execute(
            sa.text("""UPDATE tracked_accounts SET active = false
                        WHERE platform = :p AND lower(handle) = lower(:h)"""),
            {"p": platform, "h": handle},
        )

    # Non-France francophone sources that predate the detector. Kept narrow and
    # explicit: a broad LIKE over every marker would risk demoting a French
    # account whose handle merely contains one of these strings.
    conn.execute(sa.text("""
        UPDATE social_posts SET source_scope = 'global'
         WHERE source_scope = 'fr'
           AND (lower(coalesce(author, '')) LIKE '%-canada'
             OR lower(coalesce(author, '')) LIKE '%canada%'
             OR lower(coalesce(author, '')) LIKE '%quebec%'
             OR lower(coalesce(author, '')) LIKE '%.ca')
    """))


def downgrade() -> None:
    conn = op.get_bind()
    for platform, handle in _GLOBAL_ACCOUNTS:
        conn.execute(
            sa.text("""UPDATE tracked_accounts SET active = true
                        WHERE platform = :p AND lower(handle) = lower(:h)"""),
            {"p": platform, "h": handle},
        )
    # source_scope is not restored: the pre-detector values were wrong.
