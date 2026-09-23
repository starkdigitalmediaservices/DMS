"""Entity-graph gap found live 2026-09-23: Entity 360's "Linked entities"
panel was 0 for every entity in the system.

auto_extract_entities_from_facts is the only automatic writer, and it only
ever creates entity->FACT edges (target_type="fact"). Nothing ever created
an entity->entity edge: 1,906 of the 1,910 edges in the live database were
`mentioned_in` to a fact, and all 4 entity-to-entity edges were hand-made
during testing. The relationship half of the graph -- schema, API, tier
policy, approval workflow, UI -- existed with no producer.

The relationships were already in the data. Fact.row_group_id records real
row identity, written at extraction time (not re-derived from geometry), so
a person field and a property field sharing a row_group_id came off the
SAME physical table row -- in a Waqf register that is a manages-relationship.

The reason this needs a junk filter rather than a naive join: of 778 row
groups in the live demo tenant holding both a person and a property field,
348 have `..` / `.` / `Do.` / `-` on one or both sides -- ditto and
continuation placeholders. Creating edges for those would produce entities
literally named ".." linked to each other, repeating the over-linking
failure the graph already suffers from elsewhere.
"""
import uuid

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.entity_node import EntityNode
from app.models.entity_edge import EntityEdge
from app.services.entity_graph_service import auto_extract_relationships_from_facts


async def _make_doc(db):
    tenant_id = uuid.uuid4()
    db.add_all([
        Tenant(id=tenant_id, name=f"Rel Tenant {uuid.uuid4().hex[:6]}"),
        User(id=uuid.uuid4(), tenant_id=tenant_id, email=f"rel_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw"),
    ])
    await db.flush()
    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="register.pdf", status="indexed")
    ver = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="register.pdf",
    )
    db.add_all([doc, ver])
    await db.flush()
    doc.current_version_id = ver.id
    await db.flush()
    return tenant_id, doc.id, ver.id


def _fact(tenant_id, doc_id, ver_id, field, value, row_group_id, conf=0.95):
    return Fact(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc_id, version_id=ver_id,
        field_name=field, value={"v": value}, confidence=conf, row_group_id=row_group_id,
    )


async def _edges(db, tenant_id):
    res = await db.execute(
        select(EntityEdge).where(
            EntityEdge.tenant_id == tenant_id,
            EntityEdge.evidence_fact_id.is_(None) | EntityEdge.target_node_id.isnot(None),
        )
    )
    return [e for e in res.scalars().all() if e.target_node_id is not None]


@pytest.mark.asyncio
async def test_person_and_property_in_one_row_become_a_relationship():
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            rg = uuid.uuid4()
            db.add_all([
                _fact(tid, did, vid, "mutawalli_name", "Abdul Rahman Sheikh", rg),
                _fact(tid, did, vid, "wakf_name", "Masjid-e-Noor, Latur Naka", rg),
            ])
            await db.flush()

            created = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert created == 1, f"expected one manages-relationship, got {created}"
            edges = await _edges(db, tid)
            assert len(edges) == 1
            e = edges[0]
            assert e.edge_type == "manages"
            assert e.tier == 2, "a relationship read off one physical row is mechanical, not a legal identity claim"
            assert e.status == "machine"

            nodes = (await db.execute(select(EntityNode).where(EntityNode.tenant_id == tid))).scalars().all()
            by_id = {n.id: n for n in nodes}
            assert by_id[e.source_node_id].entity_type == "person"
            assert by_id[e.target_node_id].entity_type == "property"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_ditto_placeholders_are_rejected():
    """348 of 778 live candidates look like this. They must produce nothing."""
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            for person, prop in [("..", ".."), ("Do.", "Masjid-e-Noor"), ("Yusuf Ali Khan", ".."), ("-", "-")]:
                rg = uuid.uuid4()
                db.add_all([
                    _fact(tid, did, vid, "mutawalli_name", person, rg),
                    _fact(tid, did, vid, "wakf_name", prop, rg),
                ])
            await db.flush()

            created = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert created == 0, f"placeholder rows must not become relationships, got {created}"
            assert await _edges(db, tid) == []
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_relationships_do_not_cross_row_boundaries():
    """The over-linking bug this pass must not repeat: a person in row A must
    NOT be linked to a property in row B just because they share a document."""
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            rg_a, rg_b = uuid.uuid4(), uuid.uuid4()
            db.add_all([
                _fact(tid, did, vid, "mutawalli_name", "Abdul Rahman Sheikh", rg_a),
                _fact(tid, did, vid, "wakf_name", "Masjid-e-Noor", rg_a),
                _fact(tid, did, vid, "mutawalli_name", "Yusuf Ali Khan", rg_b),
                _fact(tid, did, vid, "wakf_name", "Dargah Hazrat Shah Wali", rg_b),
            ])
            await db.flush()

            created = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert created == 2, "two rows, two relationships -- not a 2x2 cross product"
            edges = await _edges(db, tid)
            assert len(edges) == 2

            nodes = {n.id: n.label for n in
                     (await db.execute(select(EntityNode).where(EntityNode.tenant_id == tid))).scalars().all()}
            pairs = {(nodes[e.source_node_id], nodes[e.target_node_id]) for e in edges}
            assert pairs == {
                ("Abdul Rahman Sheikh", "Masjid-e-Noor"),
                ("Yusuf Ali Khan", "Dargah Hazrat Shah Wali"),
            }, f"rows got crossed: {pairs}"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_repeated_name_collapses_onto_one_node():
    """"Muslim Panch Managing." appears in ~40 live rows. One node, not 40."""
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            for prop in ["Graveyard of Fakirs", "Chilla Peer Saheb", "Jama Masjid Arvi"]:
                rg = uuid.uuid4()
                db.add_all([
                    _fact(tid, did, vid, "mutawalli_name", "Muslim Panch Managing", rg),
                    _fact(tid, did, vid, "wakf_name", prop, rg),
                ])
            await db.flush()

            created = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert created == 3
            persons = (await db.execute(
                select(EntityNode).where(EntityNode.tenant_id == tid, EntityNode.entity_type == "person")
            )).scalars().all()
            assert len(persons) == 1, f"the same manager became {len(persons)} nodes"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_row_with_only_one_side_creates_nothing():
    """A property with no named manager is not a relationship."""
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            rg = uuid.uuid4()
            db.add_all([_fact(tid, did, vid, "wakf_name", "Masjid-e-Noor", rg)])
            await db.flush()

            created = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert created == 0
            assert await _edges(db, tid) == []
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_rerunning_does_not_duplicate_relationships():
    """Re-ingest / backfill must be idempotent, or every re-run doubles the graph."""
    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            rg = uuid.uuid4()
            db.add_all([
                _fact(tid, did, vid, "mutawalli_name", "Abdul Rahman Sheikh", rg),
                _fact(tid, did, vid, "wakf_name", "Masjid-e-Noor", rg),
            ])
            await db.flush()

            first = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()
            second = await auto_extract_relationships_from_facts(db, tid, did, vid)
            await db.flush()

            assert first == 1
            assert second == 0, "second run must be a no-op"
            assert len(await _edges(db, tid)) == 1
        finally:
            await db.rollback()
            await db.close()
