"""Authentication & RBAC: bcrypt password hashing, JWT tokens, FastAPI deps.

Two roles: 'admin' (full access) and 'user' (read + scrapes + own chat/search
history). Tokens are signed with settings.secret_key and carry {sub, role, exp}.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User

settings = get_settings()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12

# auto_error=False so we can also build an optional dependency for scoping
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ── Login brute-force throttle (Redis fixed-window) ───────
# Per-EMAIL is the robust limit (an attacker can't change the account they
# target). Per-IP is a generous net — kept lenient so a whole office behind one
# NAT IP isn't locked out. Only FAILED attempts are counted; success clears the
# email bucket. Fails open if Redis is unavailable (never blocks real logins).
LOGIN_WINDOW = 900            # 15 minutes
LOGIN_MAX_PER_EMAIL = 5
LOGIN_MAX_PER_IP = 50


def _redis_client():
    try:
        import redis as _redis
        return _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
    except Exception:
        return None


def _login_key(kind: str, value: str) -> str:
    import time
    window = int(time.time()) // LOGIN_WINDOW
    return f"login_fail:{kind}:{value}:{window}"


def login_throttled(email: str, ip: str) -> bool:
    r = _redis_client()
    if not r:
        return False
    try:
        e = int(r.get(_login_key("email", email)) or 0)
        i = int(r.get(_login_key("ip", ip)) or 0)
        return e >= LOGIN_MAX_PER_EMAIL or i >= LOGIN_MAX_PER_IP
    except Exception:
        return False


def record_login_failure(email: str, ip: str) -> None:
    r = _redis_client()
    if not r:
        return
    try:
        for k in (_login_key("email", email), _login_key("ip", ip)):
            pipe = r.pipeline()
            pipe.incr(k)
            pipe.expire(k, LOGIN_WINDOW)
            pipe.execute()
    except Exception:
        pass


def clear_login_failures(email: str) -> None:
    r = _redis_client()
    if not r:
        return
    try:
        r.delete(_login_key("email", email))
    except Exception:
        pass


# ── Passwords ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Tokens ────────────────────────────────────────────────

def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user.id), "role": user.role, "email": user.email, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


# ── Dependencies ──────────────────────────────────────────

async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    payload = _decode(token)
    user = await db.get(User, int(payload.get("sub", 0)))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


async def get_optional_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """For per-user scoping on otherwise-public endpoints — never raises."""
    if not token:
        return None
    try:
        payload = _decode(token)
        user = await db.get(User, int(payload.get("sub", 0)))
        return user if (user and user.is_active) else None
    except HTTPException:
        return None


# ── Daily AI-generation quota ─────────────────────────────
# A regular user may force ONE fresh LLM generation per feature per day
# (the cached result is always free/unlimited). Admins are unlimited.
# Fails open if Redis is down. Counted per UTC day.

def enforce_daily_generation(user: User, feature: str) -> None:
    if user.role == "admin":
        return
    r = _redis_client()
    if not r:
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"gen_quota:{feature}:{user.id}:{day}"
    try:
        used = r.incr(key)
        if used == 1:
            r.expire(key, 90000)  # ~25h — safely covers the UTC day
    except Exception:
        return
    if used > 1:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You've already used your one daily AI generation for this. "
            "It resets tomorrow — the latest result is still shown.",
        )


def daily_generation_available(user: User, feature: str) -> bool:
    """Read-only: can this user still force a fresh generation today? (admins: always)."""
    if user.role == "admin":
        return True
    r = _redis_client()
    if not r:
        return True
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return int(r.get(f"gen_quota:{feature}:{user.id}:{day}") or 0) < 1
    except Exception:
        return True


def daily_gen_guard(feature: str):
    """Dependency: when ?refresh=true, enforce the per-user daily quota.
    Reads the same `refresh` query param the endpoint already declares."""
    async def _dep(refresh: bool = False, user: User = Depends(get_current_user)) -> User:
        if refresh:
            enforce_daily_generation(user, feature)
        return user
    return _dep


# ── Seeding ───────────────────────────────────────────────

async def ensure_seed_admin(db: AsyncSession) -> None:
    """Create the first admin from env if no users exist yet. Idempotent."""
    existing = await db.execute(select(User.id).limit(1))
    if existing.first():
        return
    email = settings.seed_admin_email
    password = settings.seed_admin_password
    if not email or not password:
        return
    name = (getattr(settings, "seed_admin_name", "") or "Administrator").strip()
    db.add(User(name=name, email=email.lower().strip(),
                hashed_password=hash_password(password), role="admin"))
    await db.commit()
