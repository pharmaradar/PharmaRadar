"""Delete account landing pages that were stored as posts.

Ten rows in social_posts were `facebook.com/<page>` with empty text, zero
engagement and a NULL posted_at — the account's home page, captured as if it
were content.

They matter more than their count suggests. The tracked-accounts panel now
reports how many posts each account has produced, so the client can see a handle
that is silently collecting nothing. A landing-page row gives a dead account a
count of 1 and makes it look alive, which defeats the one signal that exposes a
wrong handle.

`tinyfish_social.looks_like_post` now rejects these at ingest, so this only has
to clean up what is already stored. The predicate below is deliberately narrower
than that function: it removes only rows that have no text AND no post-shaped
path, so nothing carrying real content is touched.

Revision ID: 033
Revises: 032
"""
from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None

_POST_MARKERS = ("/posts/", "/videos/", "/reel/", "/photo", "/permalink",
                 "story_fbid=", "/status/", "/p/")


def upgrade() -> None:
    conn = op.get_bind()
    clause = " AND ".join(f"post_url NOT LIKE '%{marker}%'" for marker in _POST_MARKERS)
    result = conn.execute(sa.text(f"""
        DELETE FROM social_posts
         WHERE COALESCE(TRIM(text), '') = ''
           AND {clause}
    """))
    print(f"    removed {result.rowcount} landing-page rows")


def downgrade() -> None:
    # The rows carried no content, so there is nothing to restore. Re-scraping
    # would recreate them only if the ingest guard were also reverted.
    pass
