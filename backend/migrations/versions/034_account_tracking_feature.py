"""Give account tracking its own identity: richer accounts, and a real join key.

Two changes, both needed before account tracking can be a feature rather than a
list of handles.

1. `tracked_accounts` gains the fields a person needs to manage an account they
   chose to watch: who it actually is, why it is tracked, and — critically —
   when it was last scanned and how that went. Without the last two, "is this
   account being collected?" is unanswerable except by reading logs.

2. `social_posts.tracked_account_id` is a real foreign key. Attribution
   previously went through the author string plus a `tracked:<platform>:<handle>`
   query tag, which works until an account is renamed, a handle is reused across
   platforms, or a lane forgets the tag. A per-account report has to be exact, so
   the link becomes a column rather than a convention.

The FK is nullable and ON DELETE SET NULL: the vast majority of social_posts come
from keyword search and belong to no account, and removing a tracked account must
not delete the posts already collected from it.

Revision ID: 034
Revises: 033
"""
from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_accounts", sa.Column("full_name", sa.String(255), nullable=True))
    op.add_column("tracked_accounts", sa.Column("notes", sa.Text(), nullable=True))
    # Why this account is watched: kol | institution | pharma | patient_association | media | other
    op.add_column("tracked_accounts", sa.Column("role", sa.String(32), nullable=True))
    op.add_column("tracked_accounts",
                  sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True))
    # ok | empty | error — 'empty' is the interesting one: the scan ran and the
    # handle returned nothing, which is what a wrong slug looks like.
    op.add_column("tracked_accounts", sa.Column("last_scan_status", sa.String(16), nullable=True))
    op.add_column("tracked_accounts",
                  sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"))

    op.add_column("social_posts", sa.Column("tracked_account_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_social_posts_tracked_account", "social_posts", "tracked_accounts",
        ["tracked_account_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_social_posts_tracked_account_id", "social_posts",
                    ["tracked_account_id"])

    # Backfill the FK from the attribution that already works, so existing posts
    # appear under their account instead of the feature looking empty on day one.
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE social_posts sp
           SET tracked_account_id = ta.id
          FROM tracked_accounts ta
         WHERE sp.tracked_account_id IS NULL
           AND sp.platform = ta.platform
           AND (
                LOWER(LTRIM(TRIM(sp.author), '@')) = LOWER(LTRIM(TRIM(ta.handle), '@'))
             OR LOWER(sp.query) = 'tracked:' || ta.platform || ':' || LOWER(LTRIM(TRIM(ta.handle), '@'))
           )
    """))
    conn.execute(sa.text("""
        UPDATE tracked_accounts ta
           SET post_count = COALESCE(
                 (SELECT COUNT(*) FROM social_posts sp
                   WHERE sp.tracked_account_id = ta.id), 0)
    """))


def downgrade() -> None:
    op.drop_index("ix_social_posts_tracked_account_id", table_name="social_posts")
    op.drop_constraint("fk_social_posts_tracked_account", "social_posts", type_="foreignkey")
    op.drop_column("social_posts", "tracked_account_id")
    for column in ("post_count", "last_scan_status", "last_scanned_at",
                   "role", "notes", "full_name"):
        op.drop_column("tracked_accounts", column)
