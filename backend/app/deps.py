import uuid
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from .database import AsyncSessionLocal, AppSessionLocal, get_db, set_request_gucs, _reset_session_tenant_context  # noqa: F401 — get_db re-exported; 13 API route files import it from here, not from .database directly
from .services.auth_service import verify_token
from .services import department_service
from .schemas.auth import TokenPayload
from .permissions import check_key, legacy_access, role_grants

bearer_scheme = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> TokenPayload:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_token(credentials.credentials)
    if payload.type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    live = await load_live_access(payload.sub, payload.tenant_id)
    if live is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The role claim is only a snapshot from login; every permission check
    # below uses the role as it is now, so a demotion takes effect on the
    # next request instead of whenever the token chain happens to end.
    return payload.model_copy(update=live)


async def load_live_access(user_id: str, tenant_id: str) -> dict | None:
    """The caller's role as it is right now: the legacy persona string plus
    the custom role (migration 0056) it points at. None if the user is gone.
    One query, same session pattern as load_live_role. A user with no
    role_id yet gets their old persona's access (permissions.legacy_access)
    until R13 makes role_id mandatory."""
    async with AppSessionLocal() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": str(tenant_id)}
            )
            res = await session.execute(
                text(
                    "SELECT u.role::text, r.id::text, r.name, r.is_system, r.all_departments, r.permissions "
                    "FROM iam_dg_users u LEFT JOIN iam_dg_roles r "
                    "  ON r.id = u.role_id AND r.tenant_id = u.tenant_id "
                    "WHERE u.id = CAST(:u AS uuid) AND u.tenant_id = CAST(:t AS uuid)"
                ),
                {"u": str(user_id), "t": str(tenant_id)},
            )
            row = res.first()
        finally:
            await _reset_session_tenant_context(session)
    if row is None:
        return None
    role, role_id, role_name, is_system, all_departments, perms = row
    if role_id is None:
        # No custom role yet: the old persona's exact access (see legacy_access).
        is_system, all_departments, perms, role_name = legacy_access(role)
    return {
        "role": role,
        "role_id": role_id,
        "role_name": role_name,
        "is_admin": bool(is_system),
        "all_departments": bool(all_departments),
        "permissions": list(perms or []),
    }


async def load_live_role(user_id: str, tenant_id: str) -> str | None:
    async with AppSessionLocal() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": str(tenant_id)}
            )
            res = await session.execute(
                text("SELECT role::text FROM iam_dg_users WHERE id = CAST(:u AS uuid) AND tenant_id = CAST(:t AS uuid)"),
                {"u": str(user_id), "t": str(tenant_id)},
            )
            return res.scalar_one_or_none()
        finally:
            await _reset_session_tenant_context(session)

async def require_tenant_access(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    if not current_user or not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")
    return current_user


def require_role(*allowed_roles: str):
    """T50 — reusable role-gate dependency, e.g. Depends(require_role('it_admin', 'auditor')).
    Replaces the old pattern of a plain function called manually inside a
    handler body (admin.py's require_admin), which doesn't compose across
    many endpoints for six personas.
    """
    async def _check(current_user: TokenPayload = Depends(require_tenant_access)) -> TokenPayload:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(allowed_roles)}",
            )
        return current_user
    return _check

def require_permission(key: str):
    """Custom-roles gate, e.g. Depends(require_permission("facts.review")).
    Replaces require_role (TASKS.md R3). The key is checked against the
    catalogue when the route module loads, so a typo fails at startup
    rather than silently denying everyone."""
    check_key(key)

    async def _check(current_user: TokenPayload = Depends(require_tenant_access)) -> TokenPayload:
        if not role_grants(current_user.is_admin, current_user.permissions, key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role does not allow this action ({key})",
            )
        return current_user
    return _check

async def get_tenant_db(
    current_user: TokenPayload = Depends(require_tenant_access),
):
    """D-2 fix — every real authenticated request should go through this,
    not plain get_db(): it's the restricted, RLS-enforced connection
    (AppSessionLocal) with app.current_tenant_id actually set from the
    caller's own verified JWT, not just correctly-written policies sitting
    disconnected from the request path (docs/decisions/D2_tenant_isolation_security_review.md,
    Finding 2). Session-scoped (is_local=false) so it survives a mid-request
    db.commit() -- a real pattern in this codebase, not a hypothetical --
    and _reset_session_tenant_context (called on every exit path) is what
    keeps that safe on a pooled connection; see its own docstring."""
    async with AppSessionLocal() as session:
        try:
            await set_request_gucs(session, {"app.current_tenant_id": str(current_user.tenant_id)})
            # Department scope (migration 0053's RLS policies) -- computed
            # under tenant context only, then applied for the rest of the
            # request, including after any mid-request commit.
            await department_service.apply_request_scope(
                session, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub), current_user
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await _reset_session_tenant_context(session)
            await session.close()

async def get_request_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]
    return request.client.host if request.client else "127.0.0.1"