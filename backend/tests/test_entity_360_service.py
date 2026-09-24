"""Regression test for a real cross-tenant leak found and fixed 2026-09-02
(D-2 security review): get_entity_360_view()'s node/fact lookups had no
tenant_id filter, so any EntityEdge pointing at another tenant's
node/fact -- however it got created -- would have its real content
returned. Tested independently of the create_edge() write-side fix (this
test inserts the cross-tenant edge directly, not through create_edge()),
since a pre-existing bad row or any other future write path should still
be caught here as defense in depth, not rely on the write side alone."""
import uuid

import pytest

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.entity_node import EntityNode
from app.models.entity_edge import EntityEdge
from app.services.entity_360_service import get_entity_360_view


async def _make_tenant_with_fact_and_node(db, secret_value):
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    tenant = Tenant(id=tenant_id, name=f"E360 Tenant {uuid.uuid4().hex[:6]}")
    user = User(id=actor_id, tenant_id=tenant_id, email=f"e360_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw")
    db.add_all([tenant, user])
    await db.flush()
    # actor_id returned below so callers have a real, FK-satisfying user
    # to attribute a directly-inserted edge to.

    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="e360 doc", status="indexed")
    version = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="e360.pdf",
    )
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version.id
    await db.flush()

    fact = Fact(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
        field_name="secret_field", value={"v": secret_value}, confidence=0.9, status="machine",
    )
    node = EntityNode(id=uuid.uuid4(), tenant_id=tenant_id, entity_type="person", label="E360 Person", attributes={})
    db.add_all([fact, node])
    await db.flush()

    return tenant_id, actor_id, node.id, fact.id


@pytest.mark.asyncio
async def test_entity_360_never_returns_another_tenants_fact_content():
    async with AsyncSessionLocal() as db:
        try:
            tenant_a, actor_a, node_a, _fact_a = await _make_tenant_with_fact_and_node(db, "tenant A's own value")
            tenant_b, _actor_b, _node_b, fact_b = await _make_tenant_with_fact_and_node(db, "TENANT B SECRET -- must never leak")

            # Directly insert a cross-tenant edge, bypassing create_edge()'s
            # own validation entirely -- this test must catch the leak even
            # if some other future write path (or pre-existing bad data)
            # produces a row create_edge() itself would now refuse.
            bad_edge = EntityEdge(
                id=uuid.uuid4(), tenant_id=tenant_a, edge_type="mentioned_in", tier=2,
                source_node_id=node_a, target_type="fact", target_fact_id=fact_b,
                status="machine", created_by_actor_id=actor_a,
            )
            db.add(bad_edge)
            await db.flush()

            view = await get_entity_360_view(db, tenant_a, node_a)

            linked_fact_ids = {lf["fact"]["fact_id"] for lf in view["linked_facts"]}
            assert str(fact_b) not in linked_fact_ids
            for lf in view["linked_facts"]:
                assert "TENANT B SECRET" not in str(lf["fact"].get("value", ""))
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_relationship_evidence_is_shown_and_appears_in_lists_every_document():
    """Found live 2026-09-24: 60% of entities showed "Linked facts (0)"
    although their relationships carried the document value they were read
    from. The view now surfaces that evidence (with page), and "appears_in"
    lists each document mentioning the entity -- linked, or a value reading
    exactly as its name."""
    from app.models.fact_region import FactRegion
    from app.models.page import DocumentPage

    async with AsyncSessionLocal() as db:
        try:
            tenant_id = uuid.uuid4()
            db.add(Tenant(id=tenant_id, name=f"E360b {uuid.uuid4().hex[:6]}"))
            await db.flush()
            docs = []
            for title in ("register-a.pdf", "register-b.pdf"):
                doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title=title, status="indexed")
                version = DocumentVersion(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1,
                                          s3_path="x", file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename=title)
                db.add_all([doc, version])
                await db.flush()
                doc.current_version_id = version.id
                page = DocumentPage(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
                                    page_number=3, width=600, height=800)
                db.add(page)
                await db.flush()
                docs.append((doc, version, page))

            def fact_on(i, name, value):
                doc, version, page = docs[i]
                f = Fact(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
                         field_name=name, value={"v": value}, confidence=0.9, status="machine")
                return f, FactRegion(id=uuid.uuid4(), tenant_id=tenant_id, fact_id=f.id, page_id=page.id,
                                     x0=0.1, y0=0.1, x1=0.3, y1=0.15)

            evidence, r1 = fact_on(0, "wakf_name", "Dargah Hazrat Shah Wali")
            same_name, r2 = fact_on(1, "wakf_name", "  dargah hazrat shah wali ")  # never linked
            unrelated, r3 = fact_on(1, "wakf_name", "Idgah Maidan")
            db.add_all([evidence, same_name, unrelated])
            await db.flush()
            db.add_all([r1, r2, r3])
            prop = EntityNode(id=uuid.uuid4(), tenant_id=tenant_id, entity_type="property", label="Dargah Hazrat Shah Wali", attributes={})
            person = EntityNode(id=uuid.uuid4(), tenant_id=tenant_id, entity_type="person", label="Yusuf Ali Khan", attributes={})
            db.add_all([prop, person])
            await db.flush()
            db.add(EntityEdge(id=uuid.uuid4(), tenant_id=tenant_id, edge_type="manages", tier=2, source_node_id=person.id,
                              target_type="entity", target_node_id=prop.id, status="machine",
                              created_by_policy_version="test", evidence_fact_id=evidence.id))
            await db.flush()

            view = await get_entity_360_view(db, tenant_id, prop.id)

            link = view["linked_entities"][0]
            assert link["evidence"]["fact_id"] == str(evidence.id)
            assert link["evidence"]["document_title"] == "register-a.pdf" and link["evidence"]["page_numbers"] == [3]

            assert [lf["fact"]["fact_id"] for lf in view["linked_facts"]] == [str(evidence.id)]
            assert view["linked_facts"][0]["via"] == {"edge_type": "manages", "other_label": "Yusuf Ali Khan"}

            appears = {a["document_title"]: a for a in view["appears_in"]}
            assert set(appears) == {"register-a.pdf", "register-b.pdf"}
            assert appears["register-a.pdf"]["linked"] is True and appears["register-a.pdf"]["pages"] == [3]
            assert appears["register-b.pdf"]["linked"] is False
            assert [m["fact_id"] for m in appears["register-b.pdf"]["mentions"]] == [str(same_name.id)]  # not "Idgah Maidan"
            assert view["appears_in"][0]["document_title"] == "register-a.pdf"  # linked documents first
        finally:
            await db.rollback()
