"""Unlink posts credited to an account that did not write them.

The Instagram profile scraper also returns posts the account was tagged in or
collaborated on. Those were linked to the tracked account by URL, so two posts
(`mieux.media`, `themonodie`) were filed under Fondation ARC and Ligue contre le
cancer — crediting both with reach they never had, and putting words in their
mouth in any report built on the link.

`tasks.accounts._authored_by` now verifies authorship at collection time. This
repairs the rows already stored.

The posts themselves are kept. They are real French posts and belong in the
corpus; only the claim about who wrote them is wrong, so the FK is cleared
rather than the row deleted.

Revision ID: 037
Revises: 036
"""
from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Compare the post's author against the handle of the account it is linked
    # to, ignoring case and a leading '@'. Anything that disagrees was never
    # written by that account.
    result = conn.execute(sa.text("""
        UPDATE social_posts sp
           SET tracked_account_id = NULL
          FROM tracked_accounts ta
         WHERE sp.tracked_account_id = ta.id
           AND sp.platform = 'instagram'
           AND LOWER(LTRIM(TRIM(COALESCE(sp.author, '')), '@'))
               IS DISTINCT FROM LOWER(LTRIM(TRIM(ta.handle), '@'))
    """))
    print(f"  unlinked {result.rowcount} mis-attributed Instagram posts")

    # Counters are derived from the link, so they have to be recomputed.
    conn.execute(sa.text("""
        UPDATE tracked_accounts ta
           SET post_count = COALESCE(
                 (SELECT COUNT(*) FROM social_posts sp
                   WHERE sp.tracked_account_id = ta.id), 0)
    """))


def downgrade() -> None:
    # Re-attaching posts to accounts that did not write them would restore a
    # falsehood, so this is deliberately not reversible.
    pass
