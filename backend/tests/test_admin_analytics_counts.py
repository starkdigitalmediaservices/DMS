"""Admin Panel analytics counts (GET /admin/analytics).

Found 2026-09-23 on the real tenant: "Top Uploaders" credited every user
with every document in the tenant (7 users x 45 documents each, when one
person uploaded all 45), and "Storage Per Tenant" joined documents and
users onto the same row, multiplying the totals by the user count (448
documents / 1.7 GB shown for a real 45 / 231 MB).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal
from app.main import app
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password


@pytest.mark.asyncio
async def test_uploaders_and_storage_are_counted_per_document_once():
    t = uuid.uuid4()
    admin, uploader, idle = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=t, name=f"analytics-{t.hex[:6]}"))
        await db.flush()
        for uid, role in [(admin, UserRole.it_admin), (uploader, UserRole.operator), (idle, UserRole.operator)]:
            db.add(User(id=uid, tenant_id=t, email=f"{uid.hex[:10]}@analytics-test.com", full_name="x",
                        hashed_password=hash_password("x" * 12), role=role))
        await db.flush()
        # uploader: 3 documents of 100 bytes; admin: 1 of 50; idle: none.
        for creator, size in [(uploader, 100), (uploader, 100), (uploader, 100), (admin, 50)]:
            doc = Document(id=uuid.uuid4(), tenant_id=t, title="d", status="indexed", created_by=creator)
            db.add(doc)
            await db.flush()
            v = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, tenant_id=t, version_number=1,
                                s3_path="x", file_hash=uuid.uuid4().hex, file_size_bytes=size, original_filename="d.pdf")
            db.add(v)
            await db.flush()
            doc.current_version_id = v.id
        await db.commit()

    token = create_access_token(admin, t, "it_admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        r = await c.get("/api/v1/admin/analytics")
    assert r.status_code == 200, r.text
    body = r.json()

    uploaders = {u["email"].split("@")[0]: (u["doc_count"], u["total_size"]) for u in body["top_uploaders"]}
    assert uploaders[uploader.hex[:10]] == (3, 300)
    assert uploaders[admin.hex[:10]] == (1, 50)
    assert idle.hex[:10] not in uploaders  # uploaded nothing

    assert body["storage_per_tenant"] == [{
        "tenant_name": f"analytics-{t.hex[:6]}", "doc_count": 4, "total_size": 350, "user_count": 3,
    }]
