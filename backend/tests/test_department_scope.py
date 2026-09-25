"""T50 department scoping and the rest of RBAC, exercised end to end.

Before migration 0053 the only department filter was one Python line on
GET /folders; verified live 2026-09-23, an operator could list every
document in the tenant, open any of them by id, and walk the whole folder
tree. These tests go through the real HTTP stack (require_role ->
get_tenant_db -> RLS on the restricted dms_app connection), so they fail
if scope is ever enforced in fewer places than the database.

Fixture layout, one throwaway tenant:

    A/            granted to the department
      A1/         (inherits A's grant)
    P/            not granted
      G/          granted directly -- a grant below an ungranted parent
    B/            not granted
    root: doc_op_root (uploaded by the operator), doc_admin_root

Fixtures are COMMITTED through the superuser connection (AppSessionLocal
is a separate pool and can't see uncommitted rows); conftest's session
purge removes the tenant afterwards.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.database import AppSessionLocal, AsyncSessionLocal, set_request_gucs
from app.main import app
from app.models.department import Department, DepartmentFolder, DepartmentMember
from app.models.document import Document
from app.models.folder import Folder
from app.models.metadata_item import MetadataItem
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services import department_service
from app.services.auth_service import create_access_token, create_refresh_token, hash_password


def _ids(rows):
    return {r["id"] for r in rows}


@pytest_asyncio.fixture
async def world():
    t = uuid.uuid4()
    w = {"tenant": t}
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=t, name=f"scope-test-{t.hex[:6]}"))
        await db.flush()
        for key, role in [("admin", UserRole.it_admin), ("admin2", UserRole.it_admin),
                          ("op", UserRole.operator), ("op_nodept", UserRole.operator),
                          ("counsel", UserRole.legal_counsel)]:
            uid = uuid.uuid4()
            w[key] = uid
            db.add(User(id=uid, tenant_id=t, email=f"{key}-{uid.hex[:8]}@scope-test.com",
                        full_name=key, hashed_password=hash_password("x" * 12), role=role))
        await db.flush()

        for key, parent in [("A", None), ("A1", "A"), ("P", None), ("G", "P"), ("B", None)]:
            fid = uuid.uuid4()
            w[key] = fid
            db.add(Folder(id=fid, tenant_id=t, name=key, parent_id=w.get(parent) if parent else None))
            await db.flush()

        for key, folder, creator in [("doc_A", "A", "admin"), ("doc_A1", "A1", "admin"),
                                     ("doc_G", "G", "admin"), ("doc_B", "B", "admin"),
                                     ("doc_op_root", None, "op"), ("doc_admin_root", None, "admin")]:
            did = uuid.uuid4()
            w[key] = did
            db.add(Document(id=did, tenant_id=t, title=key, status="indexed",
                            folder_id=w[folder] if folder else None, created_by=w[creator]))
        await db.flush()
        db.add(MetadataItem(tenant_id=t, document_id=w["doc_B"], key="secret", value={"v": 1}, confidence_score=1.0))

        dept = Department(tenant_id=t, name="Revenue", created_by_actor_id=w["admin"])
        db.add(dept)
        await db.flush()
        w["dept"] = dept.id
        db.add(DepartmentMember(tenant_id=t, department_id=dept.id, user_id=w["op"]))
        db.add(DepartmentFolder(tenant_id=t, department_id=dept.id, folder_id=w["A"]))
        db.add(DepartmentFolder(tenant_id=t, department_id=dept.id, folder_id=w["G"]))
        await db.commit()
    return w


def _client(w, who, role):
    token = create_access_token(w[who], w["tenant"], role)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _visible(w, who, role):
    async with _client(w, who, role) as c:
        docs = (await c.get("/api/v1/documents", params={"include_all": "true"})).json()
        roots = (await c.get("/api/v1/folders")).json()
        tree = (await c.get("/api/v1/folders/tree")).json()
    return {d["id"] for d in docs}, _ids(roots), tree


@pytest.mark.asyncio
async def test_operator_sees_only_granted_subtrees_and_own_root_uploads(world):
    w = world
    docs, roots, tree = await _visible(w, "op", "operator")
    s = lambda *keys: {str(w[k]) for k in keys}

    assert docs == s("doc_A", "doc_A1", "doc_G", "doc_op_root")
    # G is granted below an ungranted parent, so for this user it's top level.
    assert roots == s("A", "G")
    assert {n["id"] for n in tree} == s("A", "G")
    assert {n["id"] for n in next(n for n in tree if n["id"] == str(w["A"]))["subfolders"]} == s("A1")


@pytest.mark.asyncio
async def test_operator_cannot_open_out_of_scope_items_by_id(world):
    w = world
    async with _client(w, "op", "operator") as c:
        for path in [f"/api/v1/documents/{w['doc_B']}", f"/api/v1/documents/{w['doc_admin_root']}",
                     f"/api/v1/folders/{w['B']}", f"/api/v1/folders/{w['P']}",
                     f"/api/v1/documents/{w['doc_B']}/chunks"]:
            assert (await c.get(path)).status_code == 404, path
        assert (await c.get(f"/api/v1/documents/{w['doc_A1']}")).status_code == 200
        # listing an ungranted folder's contents directly yields nothing
        assert (await c.get("/api/v1/documents", params={"folder_id": str(w["B"])})).json() == []


@pytest.mark.asyncio
async def test_child_rows_follow_their_documents_scope(world):
    """Chunks/pages/facts/metadata are filtered by their parent document,
    so search and extraction views can't leak through a join-less query."""
    w = world
    async with AppSessionLocal() as db:
        await set_request_gucs(db, {"app.current_tenant_id": str(w["tenant"])})
        await department_service.apply_request_scope(db, w["tenant"], w["op"], "operator")
        n = (await db.execute(select(func.count(MetadataItem.id)).where(MetadataItem.document_id == w["doc_B"]))).scalar()
        assert n == 0
    async with AppSessionLocal() as db:
        await set_request_gucs(db, {"app.current_tenant_id": str(w["tenant"])})
        await department_service.apply_request_scope(db, w["tenant"], w["admin"], "it_admin")
        n = (await db.execute(select(func.count(MetadataItem.id)).where(MetadataItem.document_id == w["doc_B"]))).scalar()
        assert n == 1


@pytest.mark.asyncio
async def test_scope_survives_a_mid_request_commit(world):
    """The settings are replayed on every transaction, so a commit that
    hands the session a different pooled connection can't widen scope."""
    w = world
    async with AppSessionLocal() as db:
        await set_request_gucs(db, {"app.current_tenant_id": str(w["tenant"])})
        await department_service.apply_request_scope(db, w["tenant"], w["op"], "operator")
        await db.commit()
        await db.close()  # connection back to the pool; next statement checks one out fresh
        n = (await db.execute(select(func.count(Document.id)))).scalar()
        assert n == 4


@pytest.mark.asyncio
async def test_tenant_wide_roles_see_everything(world):
    w = world
    for who, role in [("admin", "it_admin"), ("counsel", "legal_counsel")]:
        docs, _, _ = await _visible(w, who, role)
        assert len(docs) == 6, role


@pytest.mark.asyncio
async def test_scoped_user_without_department_sees_only_own_uploads(world):
    docs, roots, _ = await _visible(world, "op_nodept", "operator")
    assert docs == set() and roots == set()


@pytest.mark.asyncio
async def test_scoped_user_writes_stay_inside_scope(world):
    w = world
    async with _client(w, "op", "operator") as c:
        r = await c.post("/api/v1/folders", json={"name": "rootless"})
        assert r.status_code == 403
        r = await c.post("/api/v1/folders", json={"name": "sub", "parent_id": str(w["A"])})
        assert r.status_code == 201, r.text
        assert r.json()["name"] == "sub"
        # into an ungranted folder -> that folder "doesn't exist" for them
        r = await c.patch(f"/api/v1/documents/{w['doc_A']}", json={"folder_id": str(w["B"])})
        assert r.status_code == 404
        # to root, when they didn't upload it -> would vanish for the department
        r = await c.patch(f"/api/v1/documents/{w['doc_A']}", json={"folder_id": None})
        assert r.status_code == 403
        r = await c.patch(f"/api/v1/documents/{w['doc_A']}", json={"folder_id": str(w["G"])})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_role_is_read_live_not_from_the_token(world):
    """A token minted as it_admin for a user who is now an operator must be
    treated as an operator -- demotion applies on the next request."""
    w = world
    async with _client(w, "op", "it_admin") as c:
        assert (await c.post("/api/v1/departments", json={"name": "x"})).status_code == 403
        docs = (await c.get("/api/v1/documents", params={"include_all": "true"})).json()
        assert len(docs) == 4


@pytest.mark.asyncio
async def test_refresh_reissues_the_current_role(world):
    w = world
    refresh = create_refresh_token(w["op"], w["tenant"], "it_admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    from app.services.auth_service import verify_token
    assert verify_token(r.json()["access_token"]).role == "operator"


@pytest.mark.asyncio
async def test_unknown_user_token_is_rejected(world):
    token = create_access_token(uuid.uuid4(), world["tenant"], "it_admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        assert (await c.get("/api/v1/folders")).status_code == 401


@pytest.mark.asyncio
async def test_user_management(world):
    w = world
    async with _client(w, "admin", "it_admin") as c:
        email = f"new-{uuid.uuid4().hex[:8]}@scope-test.com"
        r = await c.post("/api/v1/users", json={"email": email, "full_name": "New Person", "role": "auditor"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["role"] == "auditor" and len(created["temp_password"]) >= 12

        assert (await c.post("/api/v1/users", json={"email": email, "full_name": "Dup", "role": "auditor"})).status_code == 409
        assert (await c.post("/api/v1/users", json={"email": "x" + email, "full_name": "Bad", "role": "admin"})).status_code == 422

        listed = {u["id"]: u for u in (await c.get("/api/v1/users")).json()}
        assert created["id"] in listed
        assert listed[str(w["op"])]["departments"] == [{"id": str(w["dept"]), "name": "Revenue"}]

        r = await c.patch(f"/api/v1/users/{created['id']}", json={"role": "records_officer"})
        assert r.status_code == 200 and r.json()["role"] == "records_officer"

        # can't change your own role
        assert (await c.patch(f"/api/v1/users/{w['admin']}", json={"role": "operator"})).status_code == 400
        # demoting admin2 is fine while admin remains...
        assert (await c.patch(f"/api/v1/users/{w['admin2']}", json={"role": "operator"})).status_code == 200

    # ...but then admin is the last one, and admin2 (now an operator) can't do it
    async with _client(w, "admin2", "it_admin") as c:
        assert (await c.patch(f"/api/v1/users/{w['admin']}", json={"role": "operator"})).status_code == 403

    # the new user can actually log in with the temp password
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": created["temp_password"]})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_last_it_admin_cannot_be_demoted(world):
    """With admin2 demoted, admin is the tenant's only it_admin. Through
    the API admin can't reach this (own-role guard fires first), so drive
    the service with another actor to prove the last-admin guard itself."""
    from fastapi import HTTPException
    from app.services import user_admin_service

    w = world
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE iam_dg_users SET role = 'operator' WHERE id = :u"), {"u": w["admin2"]})
        await db.commit()
    async with AppSessionLocal() as db:
        await set_request_gucs(db, {"app.current_tenant_id": str(w["tenant"])})
        with pytest.raises(HTTPException) as exc:
            await user_admin_service.update_user(db, w["tenant"], w["admin2"], w["admin"], role="operator")
        assert exc.value.status_code == 400 and "last Admin" in exc.value.detail


@pytest.mark.asyncio
async def test_department_management(world):
    w = world
    async with _client(w, "admin", "it_admin") as c:
        depts = (await c.get("/api/v1/departments")).json()
        d = next(x for x in depts if x["id"] == str(w["dept"]))
        assert {m["user_id"] for m in d["members"]} == {str(w["op"])}
        assert {f["folder_id"] for f in d["folders"]} == {str(w["A"]), str(w["G"])}

        # cross-tenant / nonexistent ids are rejected, not silently linked
        assert (await c.post(f"/api/v1/departments/{w['dept']}/members", json={"user_id": str(uuid.uuid4())})).status_code == 404
        assert (await c.post(f"/api/v1/departments/{w['dept']}/folders", json={"folder_id": str(uuid.uuid4())})).status_code == 404

        assert (await c.delete(f"/api/v1/departments/{w['dept']}/folders/{w['G']}")).status_code == 204
        assert (await c.delete(f"/api/v1/departments/{w['dept']}/folders/{w['G']}")).status_code == 404

    # revoking G takes effect on the operator's very next request
    docs, roots, _ = await _visible(w, "op", "operator")
    assert str(w["doc_G"]) not in docs and str(w["G"]) not in roots

    async with _client(w, "admin", "it_admin") as c:
        assert (await c.delete(f"/api/v1/departments/{w['dept']}/members/{w['op']}")).status_code == 204
    docs, _, _ = await _visible(w, "op", "operator")
    assert docs == {str(w["doc_op_root"])}

    async with _client(w, "op", "operator") as c:
        assert (await c.get("/api/v1/users")).status_code == 403
        assert (await c.get("/api/v1/departments")).status_code == 403


@pytest.mark.asyncio
async def test_policies_fail_closed_without_explicit_scope(world):
    """A dms_app session that sets the tenant but never sets department
    scope must see nothing -- the first draft of 0053 showed everything
    here, and /search/stream (which opens its own session) leaked the
    whole tenant to scoped users through that gap."""
    async with AppSessionLocal() as db:
        await set_request_gucs(db, {"app.current_tenant_id": str(world["tenant"])})
        assert (await db.execute(select(func.count(Document.id)))).scalar() == 0
        assert (await db.execute(select(func.count(Folder.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_streaming_search_applies_department_scope(world, monkeypatch):
    """/search/stream runs after the request's dependencies are torn down
    and builds its own session; it must carry the caller's scope."""
    from app.api.v1 import search as search_api
    seen = {}

    from app.schemas.search import SearchResponse

    async def fake_search(*, db, query, **kwargs):
        seen["docs"] = {str(i) for i in (await db.execute(select(Document.id))).scalars()}
        return SearchResponse(query=query, ai_summary="", results=[], took_ms=0)

    monkeypatch.setattr(search_api, "do_search", fake_search)
    async with _client(world, "op", "operator") as c:
        await c.post("/api/v1/search/stream", json={"query": "anything"})
    w = world
    assert seen["docs"] == {str(w[k]) for k in ("doc_A", "doc_A1", "doc_G", "doc_op_root")}


@pytest.mark.asyncio
async def test_search_cache_is_not_shared_across_scopes(world, monkeypatch):
    """Search results are cached in Redis for 5 minutes. Keyed on tenant +
    query alone, an it_admin's cached hits were served verbatim to an
    operator (found live 2026-09-23). The key must carry the caller's scope."""
    from app.services import search_service

    keys = {}

    class _Stop(Exception):
        pass

    async def capture(key):
        raise _Stop(key)

    monkeypatch.setattr(search_service, "get_cached_search", capture)
    w = world
    for who, role in [("admin", "it_admin"), ("counsel", "legal_counsel"), ("op", "operator"), ("op_nodept", "operator")]:
        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(w["tenant"])})
            await department_service.apply_request_scope(db, w["tenant"], w[who], role)
            with pytest.raises(_Stop) as exc:
                await search_service.search("q", w["tenant"], w[who], 5, None, db, "127.0.0.1", generate_summary=False)
            keys[who] = exc.value.args[0]

    assert keys["admin"] == keys["counsel"]  # same (tenant-wide) visibility may share
    assert len({keys["admin"], keys["op"], keys["op_nodept"]}) == 3


@pytest.mark.asyncio
async def test_empty_bin_requires_a_delete_role(world):
    """POST /documents/trash/cleanup permanently deletes; same roles as DELETE."""
    async with _client(world, "op", "operator") as c:
        assert (await c.post("/api/v1/documents/trash/cleanup")).status_code == 403
    async with _client(world, "counsel", "legal_counsel") as c:
        assert (await c.post("/api/v1/documents/trash/cleanup")).status_code == 403
    async with _client(world, "admin", "it_admin") as c:
        assert (await c.post("/api/v1/documents/trash/cleanup")).status_code == 200
