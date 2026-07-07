"""Seed the first admin account (so login works immediately after deploy)

Revision ID: 016
Revises: 015
Create Date: 2026-05-29

Inserts a single admin only if NO admin exists yet — idempotent and safe
alongside the env-var seed (ensure_seed_admin). Stores only the bcrypt hash
(irreversible). CHANGE THIS PASSWORD from the Profile page after first login.
"""
from alembic import op
from sqlalchemy import text

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

# Bootstrap admin — rotate the password immediately after first login.
ADMIN_EMAIL = "admin@pharmaradar.com"
ADMIN_NAME = "Administrator"
# bcrypt hash of the one-time bootstrap password (not the plaintext)
ADMIN_HASH = "$2b$12$pAoRAnGL0Fgz.KcQT5mmcuJFBQuP9Vbla4RxKuMXrL7iFdP.yB4E2"


def upgrade():
    conn = op.get_bind()
    # Only seed if there's no admin yet — never creates a duplicate
    if conn.execute(text("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")).first():
        return
    conn.execute(
        text(
            "INSERT INTO users (name, email, hashed_password, role, is_active, created_at) "
            "VALUES (:name, :email, :hash, 'admin', true, now())"
        ),
        {"name": ADMIN_NAME, "email": ADMIN_EMAIL, "hash": ADMIN_HASH},
    )


def downgrade():
    op.get_bind().execute(text("DELETE FROM users WHERE email = :e"), {"e": ADMIN_EMAIL})
