from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy import func
import uuid
from ...schemas.auth import (
    LoginRequest, TokenResponse, TokenPayload, SignUpRequest, SignUpResponse,
    ForgotPasswordRequest, ForgotPasswordResponse, ResetPasswordRequest,
    UserProfileResponse, FileTypeCount, RefreshTokenRequest,
    UpdateLocaleRequest, UpdateLocaleResponse, ChangePasswordRequest
)
from ...models.user import User
from ...models.tenant import Tenant
from ...models.document import Document
from ...models.document_version import DocumentVersion
from ...models.chunk import Chunk
from ...models.folder import Folder

from ...deps import get_db, get_tenant_db, get_request_ip, load_live_role, require_tenant_access
from ...services.auth_service import (
    verify_password, create_access_token, create_refresh_token, sign_up,
    create_password_reset_token, reset_password_with_token, change_password,
    lookup_user_by_email, establish_tenant_context,
)
from ...services.audit_service import log_action

router = APIRouter()

@router.get('/me', response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db)
):
    user_id = uuid.UUID(current_user.sub)
    tenant_id = uuid.UUID(current_user.tenant_id)
    
    # Fetch User & Tenant
    u_stmt = select(User).where(User.id == user_id)
    u_res = await db.execute(u_stmt)
    user = u_res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    t_stmt = select(Tenant).where(Tenant.id == tenant_id)
    t_res = await db.execute(t_stmt)
    tenant = t_res.scalar_one_or_none()
    tenant_name = tenant.name if tenant else "Default Organization"

    # Analytics: Folders Count
    f_res = await db.execute(select(func.count(Folder.id)).where(Folder.tenant_id == tenant_id, Folder.is_trashed == False))
    total_folders = f_res.scalar() or 0

    # Analytics: Documents Count
    d_res = await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_trashed == False))
    total_files = d_res.scalar() or 0

    # Analytics: Total Storage Size
    v_res = await db.execute(
        select(func.coalesce(func.sum(DocumentVersion.file_size_bytes), 0))
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )
    total_size_bytes = v_res.scalar() or 0

    # Analytics: Vector Chunks Count
    c_res = await db.execute(
        select(func.count(Chunk.id))
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )
    total_chunks = c_res.scalar() or 0



    # Analytics: File Types Breakdown
    t_breakdown_res = await db.execute(
        select(
            Document.title,
            Document.doc_type,
            func.coalesce(DocumentVersion.file_size_bytes, 0)
        )
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )

    type_counts: dict[str, dict] = {}
    for title, doc_type, size in t_breakdown_res.all():
        ext = "other"
        if title and "." in title:
            ext = title.rpartition(".")[2].lower()
        elif doc_type:
            ext = str(doc_type).lower()

        if ext not in type_counts:
            type_counts[ext] = {"count": 0, "size_bytes": 0}
        type_counts[ext]["count"] += 1
        type_counts[ext]["size_bytes"] += size

    file_types = [
        FileTypeCount(extension=ext, count=data["count"], size_bytes=data["size_bytes"])
        for ext, data in sorted(type_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    ]


    return UserProfileResponse(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role.value if hasattr(user.role, 'value') else str(user.role),
        locale=user.locale,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        created_at=user.created_at.strftime("%B %d, %Y") if user.created_at else "N/A",
        total_files=total_files,
        total_folders=total_folders,
        total_size_bytes=total_size_bytes,
        total_chunks=total_chunks,
        file_types_breakdown=file_types
    )

@router.patch('/me/locale', response_model=UpdateLocaleResponse)
async def update_current_user_locale(
    body: UpdateLocaleRequest,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db)
):
    """T95 — persists the user's language choice for their next login on
    any device; the frontend also mirrors this to localStorage so the
    choice applies immediately, before this call resolves."""
    user_id = uuid.UUID(current_user.sub)
    u_res = await db.execute(select(User).where(User.id == user_id))
    user = u_res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.locale = body.locale
    await db.commit()
    return UpdateLocaleResponse(locale=user.locale)

@router.post('/me/password')
async def change_current_user_password(
    body: ChangePasswordRequest,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db)
):
    """In-place password change for an already-logged-in user — the
    profile page's "Change Password" previously just linked to
    /forgot-password, forcing an unnecessary email round-trip."""
    user_id = uuid.UUID(current_user.sub)
    await change_password(user_id, body.current_password, body.new_password, db)
    await log_action(db, user_id, uuid.UUID(current_user.tenant_id), "auth.password_change")
    return {"message": "Password changed successfully"}

@router.post('/sign-up', response_model=SignUpResponse, status_code=201)
async def sign_up_user(
    body: SignUpRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Create a new tenant and an admin user."""
    new_user = await sign_up(body, db)
    ip_addr = await get_request_ip(request)

    # D-2 fix — sign_up() already committed once internally (see its own
    # comments), so the tenant context it set doesn't survive into this
    # route's own statements; the audit log insert below needs it again.
    await establish_tenant_context(db, new_user.tenant_id)
    await log_action(
        db=db,
        actor_id=new_user.user_id,
        tenant_id=new_user.tenant_id,
        action="auth.sign_up",
        resource_type="user",
        resource_id=new_user.user_id,
        ip_address=ip_addr,
        details={"email": new_user.email}
    )
    
    await db.commit()
    return new_user

@router.post('/login', response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await lookup_user_by_email(db, body.email)

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    acc = create_access_token(user.id, user.tenant_id, user.role.value)
    ref = create_refresh_token(user.id, user.tenant_id, user.role.value)

    # D-2 fix — this session has only ever had app.login_lookup_email set
    # (the email lookup above), not app.current_tenant_id; the audit log
    # insert below is the first tenant-scoped write this request makes.
    await establish_tenant_context(db, user.tenant_id)
    await log_action(db, user.id, user.tenant_id, "auth.login")
    
    from ...config import settings
    return TokenResponse(
        access_token=acc,
        refresh_token=ref,
        expires_in=settings.jwt_access_token_expire_minutes * 60
    )

@router.post('/refresh', response_model=TokenResponse)
async def refresh_token(
    body: RefreshTokenRequest | None = None,
    refresh_token: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    token = (body.refresh_token if body and body.refresh_token else None) or refresh_token
    if not token:
        raise HTTPException(status_code=400, detail="Missing refresh token")
    from ...services.auth_service import verify_token
    payload = verify_token(token)
    if payload.type != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    
    import uuid
    user_id = uuid.UUID(payload.sub)
    tenant_id = uuid.UUID(payload.tenant_id)
    
    # The refresh token's role claim is from the original login; re-read it
    # so a role change (or a removed account) applies at the next refresh.
    role = await load_live_role(payload.sub, payload.tenant_id)
    if role is None:
        raise HTTPException(status_code=401, detail="User no longer exists")

    acc = create_access_token(user_id, tenant_id, role)
    ref = create_refresh_token(user_id, tenant_id, role)
    
    from ...config import settings
    return TokenResponse(
        access_token=acc,
        refresh_token=ref,
        expires_in=settings.jwt_access_token_expire_minutes * 60
    )

@router.post('/forgot-password', response_model=ForgotPasswordResponse)
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    user = await lookup_user_by_email(db, body.email)

    if user:
        reset_token = create_password_reset_token(body.email)
        from ...services.email_service import send_password_reset_email
        await send_password_reset_email(user.email, reset_token)
        # D-2 fix — same reasoning as login(): only app.login_lookup_email
        # has been set on this session so far.
        await establish_tenant_context(db, user.tenant_id)
        await log_action(db, user.id, user.tenant_id, "auth.forgot_password", details={"email": body.email})
        await db.commit()
        
    return ForgotPasswordResponse(
        message="If an account with that email exists, a reset link has been sent."
    )

@router.post('/reset-password')
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    await reset_password_with_token(body.email, body.reset_token, body.new_password, db)
    return {"message": "Password reset successfully. You may now sign in with your new password."}