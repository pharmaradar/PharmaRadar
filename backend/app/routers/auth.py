"""Auth endpoints: login, me, and admin-only user management."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token, get_current_user, hash_password, require_admin, verify_password,
    login_throttled, record_login_failure, clear_login_failures, LOGIN_WINDOW,
)
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    name: str | None
    email: str
    role: str
    is_active: bool
    is_superadmin: bool
    created_at: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CreateUserBody(BaseModel):
    name: str | None = None
    email: str
    password: str
    role: str = "user"  # user | admin


class UpdateUserBody(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    current_password: str | None = None
    new_password: str | None = None


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, name=u.name, email=u.email, role=u.role, is_active=u.is_active,
                   is_superadmin=bool(getattr(u, "is_superadmin", False)),
                   created_at=u.created_at.isoformat() if u.created_at else "")


# ── Auth ──────────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends(),
                db: AsyncSession = Depends(get_db)):
    email = form.username.lower().strip()
    # Real client IP behind Railway's proxy is the first X-Forwarded-For hop
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")

    if login_throttled(email, ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many login attempts. Please wait a few minutes and try again.",
                            headers={"Retry-After": str(LOGIN_WINDOW)})

    rows = await db.execute(select(User).where(User.email == email))
    user = rows.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        record_login_failure(email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")

    clear_login_failures(email)   # successful login forgives prior fumbles
    return TokenOut(access_token=create_access_token(user), user=_out(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _out(user)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be at least 8 characters")
    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"ok": True}


@router.patch("/me", response_model=UserOut)
async def update_profile(body: ProfileUpdate, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Self-service profile edit: change own name, email and/or password."""
    if body.name is not None:
        user.name = body.name.strip() or None
    if body.new_password is not None:
        if not body.current_password or not verify_password(body.current_password, user.hashed_password):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
        if len(body.new_password) < 8:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be at least 8 characters")
        user.hashed_password = hash_password(body.new_password)
    if body.email is not None:
        email = body.email.lower().strip()
        if not _valid_email(email):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address")
        if email != user.email:
            clash = await db.execute(select(User.id).where(User.email == email, User.id != user.id))
            if clash.first():
                raise HTTPException(status.HTTP_409_CONFLICT, "That email is already in use")
            user.email = email
    await db.commit()
    await db.refresh(user)
    return _out(user)


# ── Admin: user management ────────────────────────────────

@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
async def list_users(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(User).order_by(User.created_at))
    return [_out(u) for u in rows.scalars().all()]


@router.post("/users", response_model=UserOut, dependencies=[Depends(require_admin)],
             status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserBody, db: AsyncSession = Depends(get_db)):
    if body.role not in ("user", "admin"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "role must be 'user' or 'admin'")
    if len(body.password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 characters")
    email = body.email.lower().strip()
    if "@" not in email or "." not in email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address")
    exists = await db.execute(select(User.id).where(User.email == email))
    if exists.first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")
    user = User(name=(body.name or "").strip() or None, email=email,
                hashed_password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _out(user)


@router.patch("/users/{user_id}", response_model=UserOut, dependencies=[Depends(require_admin)])
async def update_user(user_id: int, body: UpdateUserBody,
                      admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    protected = bool(getattr(user, "is_superadmin", False))
    if body.name is not None:
        user.name = body.name.strip() or None
    if body.email is not None:
        email = body.email.lower().strip()
        if not _valid_email(email):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address")
        if email != user.email:
            clash = await db.execute(select(User.id).where(User.email == email, User.id != user.id))
            if clash.first():
                raise HTTPException(status.HTTP_409_CONFLICT, "That email is already in use")
            user.email = email
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "role must be 'user' or 'admin'")
        if protected and body.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The super admin's role cannot be changed")
        user.role = body.role
    if body.is_active is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")
        if protected and not body.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The super admin cannot be deactivated")
        user.is_active = body.is_active
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 characters")
        user.hashed_password = hash_password(body.password)
    await db.commit()
    await db.refresh(user)
    return _out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_user(user_id: int, admin: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    user = await db.get(User, user_id)
    if user and getattr(user, "is_superadmin", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The super admin account cannot be deleted")
    if user:
        await db.delete(user)
        await db.commit()
