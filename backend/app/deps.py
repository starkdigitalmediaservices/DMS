import uuid
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from .database import AsyncSessionLocal, AppSessionLocal, get_db, set_request_gucs, _reset_session_tenant_context  # noqa: F401 — get_db re-exported; 13 API route files import it from here, not from .database directly
from .services.auth_service import verify_token
from .services import department_service
from .schemas.auth import TokenPayload

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
    live_role = await load_live_role(payload.sub, payload.tenant_id)
    if live_role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The role claim is only a snapshot from login; every permission check
    # below uses the role as it is now, so a demotion takes effect on the
    # next request instead of whenever the token chain happens to end.
    return payload.model_copy(update={"role": live_role})


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

async def get_tenant_db(
    current_user: TokenPayload = Depends(require_tenant_access),
):
    """D-2 fix — every real authenticated request should go through this,
    not plain get_db(): it's the restricted, RLS-enforced connection
    (AppSessionLocal) with app.current_tenant_id actually set from the
    caller's own verified JWT, not just correctly-written policies sitting
    disconnected from the request path (D2_tenant_isolation_security_review.md,
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
                session, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub), current_user.role
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