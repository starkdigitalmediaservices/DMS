"""Review screen through the real HTTP stack: route role gates, If-Match /
409 / 428, ETag, and department scope (RLS on the restricted dms_app
connection) for both the review document and the page image.

Fixtures are committed through the superuser connection, same as
test_department_scope.py; conftest's session purge handles the tenant.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal
from app.main import app
from app.models.department import Department, DepartmentFolder, DepartmentMember
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.folder import Folder
from app.models.page import DocumentPage
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

TABLE = "b-facts-table"


@pytest_asyncio.fixture
async def world():
    t = uuid.uuid4()
    w = {"tenant": t}
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=t, name=f"review-api-{t.hex[:6]}"))
        await db.flush()
        for key, role in [("admin", UserRole.it_admin), ("officer", UserRole.records_officer),
                          ("op", UserRole.operator), ("auditor", UserRole.auditor)]:
            w[key] = uuid.uuid4()
            db.add(User(id=w[key], tenant_id=t, email=f"{key}-{w[key].hex[:8]}@review-test.com",
                        full_name=key, hashed_password=hash_password("x" * 12), role=role))
        await db.flush()
        for key in ("A", "B"):
            w[key] = uuid.uuid4()
            db.add(Folder(id=w[key], tenant_id=t, name=key))
        await db.flush()

        for key, folder in (("doc_A", "A"), ("doc_B", "B")):
            doc = Document(id=uuid.uuid4(), tenant_id=t, title=f"{key}.pdf", status="indexed",
                           folder_id=w[folder], created_by=w["admin"], pages_total_count=1)
            version = DocumentVersion(id=uuid.uuid4(), tenant_id=t, document_id=doc.id, version_number=1,
                                      s3_path=f"{t}/{doc.id}/v/{key}.pdf", file_hash="h", file_size_bytes=1,
                                      original_filename=f"{key}.pdf")
            db.add_all([doc, version])
            await db.flush()
            doc.current_version_id = version.id
            page = DocumentPage(id=uuid.uuid4(), tenant_id=t, document_id=doc.id, version_id=version.id,
                                page_number=1, width=600, height=800)
            db.add(page)
            await db.flush()
            fact = Fact(id=uuid.uuid4(), tenant_id=t, document_id=doc.id, version_id=version.id,
                        field_name="survey_number", value={"v": "12/3"}, confidence=0.9,
                        status="in_review", row_group_id=uuid.uuid4())
            db.add(fact)
            await db.flush()
            db.add(FactRegion(id=uuid.uuid4(), tenant_id=t, fact_id=fact.id, page_id=page.id,
                              x0=0.1, y0=0.2, x1=0.4, y1=0.25))
            w[key] = doc.id

        dept = Department(tenant_id=t, name="Revenue", created_by_actor_id=w["admin"])
        db.add(dept)
        await db.flush()
        for member in ("op", "officer"):
            db.add(DepartmentMember(tenant_id=t, department_id=dept.id, user_id=w[member]))
        db.add(DepartmentFolder(tenant_id=t, department_id=dept.id, folder_id=w["A"]))
        await db.commit()
    return w


def _client(w, who):
    role = {"admin": "it_admin", "officer": "records_officer", "op": "operator", "auditor": "auditor"}[who]
    token = create_access_token(w[who], w["tenant"], role)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _base(w, doc="doc_A"):
    return f"/api/v1/documents/{w[doc]}/review"


@pytest.mark.asyncio
async def test_read_only_role_can_view_but_not_edit(world):
    w = world
    async with _client(w, "auditor") as c:
        r = await c.get(_base(w))
        assert r.status_code == 200 and r.headers["etag"] == '"1"'
        doc = r.json()
        assert doc["permissions"] == {"can_edit": False, "can_verify": False, "can_revert_all": False}
        row_id = doc["blocks"][0]["rows"][0]["id"]
        r = await c.patch(f"{_base(w)}/blocks/{TABLE}/rows/{row_id}/cells/0", json={"value": "x"},
                          headers={"If-Match": '"1"'})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_if_match_required_and_stale_write_is_409(world):
    w = world
    async with _client(w, "op") as c:
        doc = (await c.get(_base(w))).json()
        row = doc["blocks"][0]["rows"][0]
        url = f"{_base(w)}/blocks/{TABLE}/rows/{row['id']}/cells/0"
        body = {"value": "12/8", "fact_version": row["cells"][0]["fact_version"]}

        assert (await c.patch(url, json=body)).status_code == 428
        r = await c.patch(url, json=body, headers={"If-Match": '"1"'})
        assert r.status_code == 200 and r.headers["etag"] == '"2"'
        assert r.json()["blocks"][0]["rows"][0]["cells"][0]["text"] == "12/8"

        r = await c.patch(url, json={"value": "12/9"}, headers={"If-Match": '"1"'})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_document"

        r = await c.patch(f"{_base(w)}/blocks/{TABLE}/rows/nope/cells/0", json={"value": "x"}, headers={"If-Match": '"2"'})
        assert r.status_code == 404
        r = await c.patch(f"{_base(w)}/blocks/{TABLE}/rows/{row['id']}/cells/5", json={"value": "x"}, headers={"If-Match": '"2"'})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_verify_and_revert_all_role_split(world):
    w = world
    async with _client(w, "op") as op, _client(w, "officer") as officer, _client(w, "admin") as admin:
        doc = (await op.get(_base(w))).json()
        row_id = doc["blocks"][0]["rows"][0]["id"]
        r = await op.patch(f"{_base(w)}/blocks/{TABLE}/rows/{row_id}/cells/0", json={"value": "7"}, headers={"If-Match": '"1"'})
        assert r.status_code == 200

        body = {"block_id": TABLE, "row_id": row_id, "verified": True}
        assert (await op.post(f"{_base(w)}/verify", json=body, headers={"If-Match": '"2"'})).status_code == 403
        r = await officer.post(f"{_base(w)}/verify", json=body, headers={"If-Match": '"2"'})
        assert r.status_code == 200 and r.json()["blocks"][0]["rows"][0]["status"] == "VERIFIED"

        assert (await officer.post(f"{_base(w)}/revert-all", headers={"If-Match": '"3"'})).status_code == 403
        r = await admin.post(f"{_base(w)}/revert-all", headers={"If-Match": '"3"'})
        assert r.status_code == 200
        out = r.json()
        assert out["is_clean"] and out["blocks"][0]["rows"][0]["cells"][0]["text"] == "12/3"

        history = (await admin.get(f"{_base(w)}/history", params={"block_id": TABLE})).json()["entries"]
        assert [h["action"] for h in history] == ["revert_all", "verify_row", "edit_cell"]


@pytest.mark.asyncio
async def test_department_scope_applies_to_review_and_page_image(world):
    w = world
    async with _client(w, "op") as c:
        assert (await c.get(_base(w, "doc_B"))).status_code == 404
        assert (await c.get(f"{_base(w, 'doc_B')}/pages/1/image")).status_code == 404
        assert (await c.get(f"{_base(w, 'doc_B')}/history", params={"block_id": TABLE})).status_code == 404
    async with _client(w, "admin") as c:
        assert (await c.get(_base(w, "doc_B"))).status_code == 200


@pytest.mark.asyncio
async def test_documents_list_shows_progress_and_respects_scope(world):
    w = world
    async with _client(w, "admin") as c:
        items = (await c.get("/api/v1/review/documents")).json()["items"]
        mine = {i["document_id"]: i for i in items if i["document_id"] in (str(w["doc_A"]), str(w["doc_B"]))}
        assert set(mine) == {str(w["doc_A"]), str(w["doc_B"])}
        assert mine[str(w["doc_A"])]["fact_count"] == 1 and mine[str(w["doc_A"])]["in_review_count"] == 1
        assert mine[str(w["doc_A"])]["review_started"] is False
        only_b = (await c.get("/api/v1/review/documents", params={"q": "doc_B"})).json()["items"]
        assert [i["document_id"] for i in only_b] == [str(w["doc_B"])]
    async with _client(w, "op") as c:
        ids = {i["document_id"] for i in (await c.get("/api/v1/review/documents")).json()["items"]}
        assert str(w["doc_A"]) in ids and str(w["doc_B"]) not in ids
