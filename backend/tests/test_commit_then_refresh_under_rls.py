"""T96 clean-room finding, 2026-09-07 — a mid-function `db.commit()`
followed by `db.refresh(row)` on the RLS-restricted `dms_app` connection
(AppSessionLocal) reproducibly 500'd with `InvalidRequestError: Could not
refresh instance`, found live while building the accessibility Playwright
suite (a real signup call, needed to reach authenticated pages, failed
outright). Root cause: app.current_tenant_id is a Postgres session-scoped
GUC set on the physical connection, and a mid-function commit does not
reliably keep this AsyncSession pinned to that same physical connection
for its next statement -- the follow-up SELECT `db.refresh()` runs can
land on a connection with no tenant context, which dms_app's default-deny
RLS policy treats as zero visible rows.

Fixed by re-establishing tenant context (database.py::establish_tenant_context)
immediately after every mid-function commit that's followed by more
tenant-scoped work, across auth_service.sign_up, document_service (upload/
update/toggle_star/toggle_trash), folder_service (create/update/
toggle_star/toggle_trash), and chat_service.send_chat_message. This test
exercises the exact real code path (AppSessionLocal, not the superuser
AsyncSessionLocal) end to end against two of those -- the ones this bug
was actually caught on live -- rather than re-deriving the mechanism in
isolation."""
import uuid

import pytest
from sqlalchemy import delete

from app.database import AppSessionLocal, AsyncSessionLocal, set_request_gucs
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.folder import Folder
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import SignUpRequest
from app.services import document_service, folder_service
from app.services.auth_service import hash_password, sign_up


async def _make_tenant_user_doc(db):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"T96 RLS Regression Tenant {uuid.uuid4().hex[:6]}"))
    await db.flush()
    db.add(User(
        id=user_id, tenant_id=tenant_id, email=f"t96_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("x"),
    ))
    doc = Document(id=doc_id, tenant_id=tenant_id, title="rls_regression.pdf", status="indexed")
    version = DocumentVersion(
        id=version_id, tenant_id=tenant_id, document_id=doc_id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="rls_regression.pdf",
    )
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version_id
    await db.commit()
    return tenant_id, user_id, doc_id


async def _cleanup(tenant_id, doc_id):
    """toggle_star_document writes a real audit_dg_logs entry (append-only,
    FK'd to the actor user) -- same established tradeoff as this project's
    other throwaway-tenant tests: delete what's safe (the document itself),
    leave the User/Tenant rows in place rather than fight the append-only
    guarantee the audit log is deliberately built on."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            Document.__table__.update().where(Document.id == doc_id).values(current_version_id=None)
        )
        await db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc_id))
        await db.execute(delete(Document).where(Document.id == doc_id))
        await db.commit()


@pytest.mark.asyncio
async def test_toggle_star_document_survives_commit_then_refresh_under_rls():
    async with AsyncSessionLocal() as setup_db:
        tenant_id, user_id, doc_id = await _make_tenant_user_doc(setup_db)

    try:
        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(tenant_id), "app.dept_scoped": "0"})
            # Before the fix, this raised sqlalchemy.exc.InvalidRequestError
            # ("Could not refresh instance") on the internal db.refresh(doc)
            # that follows toggle_star_document's own db.commit().
            result = await document_service.toggle_star_document(db, doc_id, tenant_id, user_id)
            assert result.is_starred is True
    finally:
        await _cleanup(tenant_id, doc_id)


@pytest.mark.asyncio
async def test_toggle_star_folder_survives_commit_then_refresh_under_rls():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as setup_db:
        setup_db.add(Tenant(id=tenant_id, name=f"T96 RLS Regression Tenant {uuid.uuid4().hex[:6]}"))
        await setup_db.flush()
        setup_db.add(User(
            id=user_id, tenant_id=tenant_id, email=f"t96_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("x"),
        ))
        await setup_db.commit()

    from app.schemas.folder import FolderCreate
    folder_id = None
    try:
        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(tenant_id), "app.dept_scoped": "0"})
            created = await folder_service.create_folder(db, tenant_id, user_id, FolderCreate(name="RLS regression"))
            folder_id = created.id

        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(tenant_id), "app.dept_scoped": "0"})
            result = await folder_service.toggle_star_folder(db, folder_id, tenant_id, user_id)
            assert result.is_starred is True
    finally:
        # Same audit-log FK tradeoff as _cleanup() above: delete the
        # folder, leave User/Tenant in place.
        async with AsyncSessionLocal() as db:
            if folder_id:
                await db.execute(delete(Folder).where(Folder.id == folder_id))
                await db.commit()


@pytest.mark.asyncio
async def test_sign_up_survives_its_own_commit_then_refresh_under_rls():
    email = f"t96_signup_{uuid.uuid4().hex[:8]}@test.com"
    async with AppSessionLocal() as db:
        # Before the fix, this raised the same InvalidRequestError on
        # sign_up()'s own db.refresh(user) -- a real production 500 on
        # every real signup, not just a test artifact.
        result = await sign_up(SignUpRequest(full_name="T96 Regression", email=email, password="x" * 8), db)
    assert result.email == email
    # No cleanup: sign_up's own audit-log call (in the auth.py route, not
    # this service function) is what normally blocks deleting a throwaway
    # signup elsewhere in this suite -- this test calls sign_up() directly
    # and never reaches that route, so nothing here writes an audit event,
    # but the tenant/user are still real rows. Left in place deliberately,
    # matching this project's established throwaway-test-account pattern.
