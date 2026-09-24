from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_node import EntityNode
from app.models.entity_edge import EntityEdge
from app.models.record import Record
from app.models.fact import Fact
from app.models.document import Document
from app.models.fact_region import FactRegion
from app.models.page import DocumentPage
from app.services.records_service import get_current_state, get_original_state

# "Appears in" also lists documents where a value reads exactly as this
# entity's name but was never linked; capped so a very common name can't
# turn one page view into a scan of the whole corpus.
_SAME_NAME_LIMIT = 200


async def _pages_by_fact(db: AsyncSession, tenant_id: UUID, fact_ids: set) -> dict:
    """{fact_id: sorted page numbers} from the facts' regions."""
    if not fact_ids:
        return {}
    res = await db.execute(
        select(FactRegion.fact_id, DocumentPage.page_number)
        .join(DocumentPage, DocumentPage.id == FactRegion.page_id)
        .where(FactRegion.tenant_id == tenant_id, FactRegion.fact_id.in_(fact_ids))
    )
    out: dict = {}
    for fact_id, page in res.all():
        out.setdefault(fact_id, set()).add(page)
    return {k: sorted(v) for k, v in out.items()}


async def get_entity_360_view(db: AsyncSession, tenant_id: UUID, node_id: UUID) -> dict:
    """T62 — one entity, everything about it: its records (versions,
    status), every linked entity/document (with tier and status), and
    enough on every fact reference to click through to its source
    (via GET /api/v1/facts/{fact_id}, T53).
    """
    node = await db.get(EntityNode, node_id)
    if not node or node.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Entity not found")

    # --- Records this entity is the subject of (T60: versions + status) ---
    records_res = await db.execute(
        select(Record).where(Record.tenant_id == tenant_id, Record.subject_node_id == node_id)
    )
    records = list(records_res.scalars().all())
    records_out = []
    for record in records:
        current = await get_current_state(db, tenant_id, record.id)
        original = await get_original_state(db, tenant_id, record.id)
        records_out.append({
            "record_id": str(record.id),
            "record_type": record.record_type,
            "current": {
                "fields": current["fields"],
                "legal_status": current["legal_status"],
                "field_provenance": {
                    field: {**prov, "amendment_id": str(prov["amendment_id"]) if prov.get("amendment_id") else None,
                            "evidence_fact_id": str(prov["evidence_fact_id"]) if prov.get("evidence_fact_id") else None}
                    for field, prov in current["field_provenance"].items()
                },
            },
            "original": {"fields": original["fields"], "legal_status": original["legal_status"]},
        })

    # --- Edges touching this node, both directions ---
    edges_res = await db.execute(
        select(EntityEdge).where(
            EntityEdge.tenant_id == tenant_id,
            or_(
                EntityEdge.source_node_id == node_id,
                and_(EntityEdge.target_type == "entity", EntityEdge.target_node_id == node_id),
            ),
        )
    )
    edges = list({e.id: e for e in edges_res.scalars().all()}.values())  # dedupe self-loops

    other_node_ids = set()
    fact_ids = set()
    for e in edges:
        if e.source_node_id != node_id:
            other_node_ids.add(e.source_node_id)
        if e.target_type == "entity" and e.target_node_id and e.target_node_id != node_id:
            other_node_ids.add(e.target_node_id)
        if e.target_type == "fact" and e.target_fact_id:
            fact_ids.add(e.target_fact_id)
        # The document value a link was read from. 60% of entities had
        # "Linked facts (0)" (live, 2026-09-24): auto-extracted relationships
        # carry their evidence here, not as a fact-targeted edge, and the
        # view only ever looked at the latter.
        if e.evidence_fact_id:
            fact_ids.add(e.evidence_fact_id)

    # tenant_id filters below are real protection, not redundant caution:
    # RLS is currently inert (see D-2 review) and create_edge() is the
    # only write path that used to be able to put a cross-tenant ID here
    # (fixed 2026-09-02) -- but a node/fact this tenant's own edge points
    # at must still always be re-verified as this tenant's own on the way
    # out, both as defense in depth and because edges created before that
    # fix could still reference another tenant's data.
    other_nodes_by_id = {}
    if other_node_ids:
        res = await db.execute(
            select(EntityNode).where(EntityNode.id.in_(other_node_ids), EntityNode.tenant_id == tenant_id)
        )
        other_nodes_by_id = {n.id: n for n in res.scalars().all()}

    facts_by_id = {}
    if fact_ids:
        res = await db.execute(select(Fact).where(Fact.id.in_(fact_ids), Fact.tenant_id == tenant_id))
        facts_by_id = {f.id: f for f in res.scalars().all()}

    # Values elsewhere that read exactly as this entity's name.
    label = (node.label or "").strip().lower()
    same_name = []
    if label:
        from sqlalchemy import func
        res = await db.execute(
            select(Fact).where(
                Fact.tenant_id == tenant_id,
                func.lower(func.btrim(Fact.value["v"].astext)) == label,
                Fact.id.notin_(fact_ids) if fact_ids else True,
            ).limit(_SAME_NAME_LIMIT)
        )
        same_name = list(res.scalars().all())

    all_facts = {**facts_by_id, **{f.id: f for f in same_name}}
    pages_by_fact = await _pages_by_fact(db, tenant_id, set(all_facts))
    docs_by_id = {}
    doc_ids = {f.document_id for f in all_facts.values()}
    if doc_ids:
        res2 = await db.execute(select(Document).where(
            Document.id.in_(doc_ids), Document.tenant_id == tenant_id, Document.is_trashed.is_(False),
        ))
        docs_by_id = {d.id: d for d in res2.scalars().all()}

    def fact_ref(fact) -> dict:
        doc = docs_by_id.get(fact.document_id)
        return {
            "fact_id": str(fact.id),
            "field_name": fact.field_name,
            "value": fact.value,
            "document_id": str(fact.document_id),
            "document_title": doc.title if doc else None,
            "page_numbers": pages_by_fact.get(fact.id, []),
        }

    linked_entities = []
    linked_facts = []
    for e in edges:
        direction = "outgoing" if e.source_node_id == node_id else "incoming"
        base = {
            "edge_id": str(e.id),
            "edge_type": e.edge_type,
            "tier": e.tier,
            "status": e.status,
            "confidence": e.confidence,
            "direction": direction,
            "evidence_fact_id": str(e.evidence_fact_id) if e.evidence_fact_id else None,
        }
        evidence = facts_by_id.get(e.evidence_fact_id) if e.evidence_fact_id else None
        if evidence is not None and evidence.document_id not in docs_by_id:
            evidence = None  # its document is trashed or out of this user's scope
        if e.target_type == "entity":
            other_id = e.target_node_id if direction == "outgoing" else e.source_node_id
            other = other_nodes_by_id.get(other_id)
            if other:
                linked_entities.append({
                    **base,
                    "other_node": {"id": str(other.id), "entity_type": other.entity_type, "label": other.label},
                    "evidence": fact_ref(evidence) if evidence else None,
                })
        elif e.target_type == "fact" and direction == "outgoing":
            fact = facts_by_id.get(e.target_fact_id)
            if fact and fact.document_id in docs_by_id:
                linked_facts.append({**base, "via": None, "fact": fact_ref(fact)})

    # The evidence behind each relationship is a linked fact too.
    listed = {lf["fact"]["fact_id"] for lf in linked_facts}
    for le in linked_entities:
        ev = le.get("evidence")
        if ev and ev["fact_id"] not in listed:
            listed.add(ev["fact_id"])
            linked_facts.append({
                "edge_id": le["edge_id"], "edge_type": le["edge_type"], "tier": le["tier"], "status": le["status"],
                "confidence": le["confidence"], "direction": le["direction"], "evidence_fact_id": ev["fact_id"],
                "via": {"edge_type": le["edge_type"], "other_label": le["other_node"]["label"]},
                "fact": ev,
            })

    # Every document that mentions this entity, with the pages.
    appears: dict = {}
    for f in all_facts.values():
        doc = docs_by_id.get(f.document_id)
        if doc is None:
            continue
        entry = appears.setdefault(doc.id, {
            "document_id": str(doc.id), "document_title": doc.title, "pages": set(), "mentions": [], "linked": False,
        })
        linked = f.id in facts_by_id
        entry["linked"] = entry["linked"] or linked
        entry["pages"].update(pages_by_fact.get(f.id, []))
        entry["mentions"].append({
            "fact_id": str(f.id), "field_name": f.field_name, "value": f.value,
            "page_numbers": pages_by_fact.get(f.id, []), "how": "linked" if linked else "same_name",
        })
    appears_in = sorted(
        ({**a, "pages": sorted(a["pages"]), "mentions": sorted(a["mentions"], key=lambda m: (m["page_numbers"] or [0])[0])[:50]}
         for a in appears.values()),
        key=lambda a: (not a["linked"], (a["document_title"] or "").lower()),
    )

    return {
        "node": {"id": str(node.id), "entity_type": node.entity_type, "label": node.label, "attributes": node.attributes},
        "records": records_out,
        "linked_entities": linked_entities,
        "linked_facts": linked_facts,
        "appears_in": appears_in,
    }
