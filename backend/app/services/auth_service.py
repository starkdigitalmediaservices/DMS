import time
import uuid
from jose import jwt, JWTError
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.config import settings
from app.database import establish_tenant_context  # noqa: F401 — re-exported; app/api/v1/auth.py imports it from here
from app.schemas.auth import TokenPayload, SignUpRequest, SignUpResponse
from app.models.role import Role
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.services.license_service import get_or_create_subscription

import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

async def lookup_user_by_email(db: AsyncSession, email: str) -> User | None:
    """D-2 fix — iam_dg_users' tenant_isolation_policy denies by default
    when no tenant context is set, which is exactly the situation here:
    login, signup, and forgot/reset-password all need to find a user BEFORE
    any tenant is known. Migration 0046 adds a second, narrowly-scoped
    permissive policy that allows exactly one row when this session var
    names its email — set it, then query normally. Session-scoped, same
    reasoning and same centralized cleanup as establish_tenant_context."""
    await db.execute(
        text("SELECT set_config('app.login_lookup_email', :e, false)"), {"e": email}
    )
    res = await db.execute(select(User).where(User.email == email))
    return res.scalar_one_or_none()

async def sign_up(body: SignUpRequest, db: AsyncSession) -> SignUpResponse:
    """Creates a new tenant and its founding user with full tenant-wide
    administrative access."""
    if await lookup_user_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create tenant
    tenant = Tenant(name=f"{body.full_name}'s Organization")
    db.add(tenant)
    await db.flush()

    # D-2 fix — signup runs with no tenant context (there's no tenant to
    # have context for until the line above creates one), but everything
    # written from here on genuinely belongs to this brand-new tenant --
    # this flow is the trusted authority establishing that, the same way
    # login/forgot-password are for app.login_lookup_email above. Without
    # this, the User and Subscription inserts below are rejected by RLS's
    # WITH CHECK (real bug, caught live: a fresh signup 500'd on the
    # billing_dg_subscription insert the instant that table got a policy).
    await establish_tenant_context(db, tenant.id)

    # Custom roles (R5): a new organisation starts clean -- exactly one
    # role, the locked Admin (holds every permission, sees every
    # department). No departments, no other roles, no templates copied.
    admin_role = Role(tenant_id=tenant.id, name="Admin", is_system=True, all_departments=True, permissions=[])
    db.add(admin_role)
    await db.flush()

    # Create user
    user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        tenant_id=tenant.id,
        # T50's persona migration (0022_personas_departments.py) moved
        # every existing 'admin' row onto 'it_admin', the modern
        # system-level-access persona -- 'admin' is kept only because
        # Postgres enum types can't drop values (see UserRole's own
        # comment). Every RBAC check in the codebase (templates.py,
        # governance.py, department_service.py's TENANT_WIDE_ROLES,
        # document_service.py's it_admin fallback, the /admin analytics
        # widget) gates on 'it_admin', never 'admin' -- so assigning the
        # legacy value here left every new tenant's founding user locked
        # out of template management, DMS Analytics, and every other
        # tenant-wide action with no self-service way to fix it (no role
        # UI exists). Found live: a fresh signup showed "This action
        # requires one of: it_admin" on its own Admin Panel.
        role=UserRole.it_admin,
        role_id=admin_role.id,
    )
    db.add(user)
    await get_or_create_subscription(db, tenant.id)  # T81 — every tenant starts on a trial
    await db.commit()

    # T96 clean-room finding, 2026-09-07: db.commit() can return this
    # session's physical connection to the pool and db.refresh() below
    # then check out a *different* one -- app.current_tenant_id is a
    # Postgres session-scoped GUC (set on the physical connection, not the
    # SQLAlchemy Session object), so it doesn't necessarily survive that
    # swap. Real bug, caught live: sign_up() started 500ing on
    # db.refresh(user) with "Could not refresh instance" the moment
    # get_db() (this route's dependency) switched from the superuser
    # connection to the RLS-enforced dms_app one in f779c5a -- under
    # dms_app's default-deny policy, a connection with no tenant context
    # sees zero rows, so the refresh SELECT found nothing. Re-establishing
    # context here, unconditionally, is the same fix pattern already used
    # elsewhere in this function for the same underlying reason.
    await establish_tenant_context(db, tenant.id)
    await db.refresh(user)

    acc = create_access_token(user.id, tenant.id, user.role.value)
    ref = create_refresh_token(user.id, tenant.id, user.role.value)

    return SignUpResponse(
        user_id=user.id,
        tenant_id=tenant.id,
        full_name=user.full_name,
        email=user.email,
        access_token=acc,
        refresh_token=ref,
        expires_in=settings.jwt_access_token_expire_minutes * 60
    )

def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    exp = int(time.time()) + (settings.jwt_access_token_expire_minutes * 60)
    to_encode = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": exp,
        "jti": str(uuid.uuid4()),
        "type": "access"
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def create_refresh_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    exp = int(time.time()) + (settings.jwt_refresh_token_expire_days * 86400)
    to_encode = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": exp,
        "jti": str(uuid.uuid4()),
        "type": "refresh"
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def create_password_reset_token(email: str) -> str:
    exp = int(time.time()) + (15 * 60)
    to_encode = {
        "sub": email,
        "type": "reset_password",
        "exp": exp,
        "jti": str(uuid.uuid4())
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

async def reset_password_with_token(email: str, token: str, new_password: str, db: AsyncSession) -> bool:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "reset_password" or payload.get("sub") != email:
            raise HTTPException(status_code=400, detail="Invalid password reset token")
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = await lookup_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=444, detail="User not found")

    user.hashed_password = hash_password(new_password)
    await db.commit()
    return True

async def change_password(user_id: uuid.UUID, current_password: str, new_password: str, db: AsyncSession) -> None:
    """The other half of password changes — an already-authenticated user
    changing their own password in place, not the forgot/reset flow (which
    is for someone who can't log in at all, and mints its own token). The
    profile page's "Change Password" used to just link to /forgot-password,
    forcing a real email round-trip for something that should be immediate.
    """
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if new_password == current_password:
        raise HTTPException(status_code=400, detail="New password must be different from the current password")

    user.hashed_password = hash_password(new_password)
    await db.commit()

def verify_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return TokenPayload(**payload)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )