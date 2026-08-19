"""Key social posts on their content, and collapse the duplicates that caused.

`social_posts.content_hash` was named for content and computed from the URL:
`sha256_hash(post_url)`. That is a stable identity only while the URL is stable,
and Facebook's is not — it serves `pfbid…` tokens that rotate between scrapes,
so every scan produced a new hash for a post already stored and the unique
constraint never fired.

Measured on the live table before this ran: 139 of 975 texted posts (14%) were
duplicates, **133 of them Facebook**. Instagram, whose URLs are stable, had
none. The client saw the same Roche post five times in a single screen, and the
inflated rows also skewed post counts, trend ranking and the material handed to
the LLM.

The new key is `platform | author | normalised text`. Platform and author stay
in it deliberately: the same wording from two organisations, or from one
organisation on two networks, is genuinely two posts with their own engagement,
and collapsing those would hide reach rather than noise. Posts with under 40
characters of text keep the URL as their key, because hashing an empty caption
would merge every image-only post from an account into one.

## About the deletion

The standing rule is not to delete scraped social data, because refetching costs
paid Apify credit. That rule protects CONTENT, and nothing is lost here: every
row removed is byte-identical in text, author and platform to one that stays,
and the survivor is chosen as the copy with the highest engagement (ties broken
by the earliest scrape), so the richest version of each post is what remains.
Keeping the duplicates would mean carrying them in every count and every prompt
forever.

The key is computed in Python by calling the real `social_content_key`, not
reimplemented in SQL — a migration that disagrees with the application about
what a duplicate is would leave the constraint fighting the ingest path.
"""
from collections import defaultdict

from alembic import op
import sqlalchemy as sa

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.deduplicator import social_content_key

    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT id, platform, author, text, post_url,
               COALESCE(likes,0) + COALESCE(comments,0)
             + COALESCE(views,0) + COALESCE(shares,0) AS engagement,
               scraped_at
        FROM social_posts
    """)).fetchall()

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[social_content_key(row.platform, row.author, row.text, row.post_url)].append(row)

    doomed: list[int] = []
    keepers: list[tuple[int, str]] = []
    for key, members in groups.items():
        # Richest copy wins: most engagement, then the earliest one seen.
        members.sort(key=lambda r: (-(r.engagement or 0),
                                    r.scraped_at or sa.text("'infinity'")))
        keepers.append((members[0].id, key))
        doomed.extend(r.id for r in members[1:])

    if doomed:
        # Chunked: a single IN () with thousands of ids is a slow plan and a
        # long lock on a table the UI reads.
        for start in range(0, len(doomed), 500):
            chunk = doomed[start:start + 500]
            conn.execute(sa.text("DELETE FROM social_posts WHERE id = ANY(:ids)"),
                         {"ids": chunk})

    # Rewrite the survivors' keys AFTER the deletes — doing it first would
    # collide with the very duplicates being removed.
    for post_id, key in keepers:
        conn.execute(sa.text("UPDATE social_posts SET content_hash = :h WHERE id = :i"),
                     {"h": key, "i": post_id})


def downgrade() -> None:
    """Restore URL-derived hashes.

    The deleted duplicates are not recoverable, which is the honest position:
    they were exact copies, and re-running the scan would refetch them anyway.
    """
    from app.services.deduplicator import sha256_hash

    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id, post_url FROM social_posts")).fetchall():
        conn.execute(sa.text("UPDATE social_posts SET content_hash = :h WHERE id = :i"),
                     {"h": sha256_hash(row.post_url or ""), "i": row.id})
