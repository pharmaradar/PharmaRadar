"""Seed the first admin account (so login works immediately after deploy)

Revision ID: 016
Revises: 015
Create Date: 2026-05-29

Inserts a single admin only if NO admin exists yet — idempotent and safe
alongside the env-var seed (ensure_seed_admin). Prefers SEED_ADMIN_EMAIL /
SEED_ADMIN_PASSWORD env vars (set these in production!) and only falls back
to the baked-in dev credentials when they're absent. Stores only the bcrypt
hash (irreversible). CHANGE THIS PASSWORD from the Profile page after first
login if you used the fallback.
"""
import os

from alembic import op
from sqlalchemy import text

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

# Fallback bootstrap admin (local dev) — rotate immediately after first login.
ADMIN_EMAIL = "admin@pharmaradar.com"
ADMIN_NAME = "Administrator"
# bcrypt hash of the one-time bootstrap password (not the plaintext)
ADMIN_HASH = "$2b$12$pAoRAnGL0Fgz.KcQT5mmcuJFBQuP9Vbla4RxKuMXrL7iFdP.yB4E2"


def _admin_from_env() -> tuple[str, str, str] | None:
    """(email, name, bcrypt_hash) from SEED_ADMIN_* env vars, or None."""
    email = (os.environ.get("SEED_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("SEED_ADMIN_PASSWORD") or ""
    if not email or not password:
        return None
    import bcrypt
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    name = (os.environ.get("SEED_ADMIN_NAME") or "Administrator").strip()
    return email, name, hashed


def upgrade():
    conn = op.get_bind()
    # Only seed if there's no admin yet — never creates a duplicate
    if conn.execute(text("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")).first():
        return
    env = _admin_from_env()
    email, name, hashed = env if env else (ADMIN_EMAIL, ADMIN_NAME, ADMIN_HASH)
    conn.execute(
        text(
            "INSERT INTO users (name, email, hashed_password, role, is_active, created_at) "
            "VALUES (:name, :email, :hash, 'admin', true, now())"
        ),
        {"name": name, "email": email, "hash": hashed},
    )


def downgrade():
    env = _admin_from_env()
    email = env[0] if env else ADMIN_EMAIL
    op.get_bind().execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
