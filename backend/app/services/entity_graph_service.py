import re
import uuid as uuid_module
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_edge import EntityEdge
from app.models.entity_node import EntityNode
from app.models.fact import Fact
from app.models.document import Document
from app.services.audit_service import log_action
from app.services.config_service import get_float

# Tier 1 (structural) and tier 2 (mention) auto-commit as machine — low-risk,
# mechanical facts ("this page contains this row", "this row names X").
# Tier 3 (identity) and tier 4 (legal) are escrowed: held until a human
# confirms them, tier 4 always regardless of confidence (Section 6).
AUTO_COMMIT_TIERS = {1, 2}
ESCROW_TIERS = {3, 4}


async def create_node(
    db: AsyncSession,
    tenant_id: UUID,
    entity_type: str,
    label: str,
    actor_id: UUID,
    attributes: Optional[Dict[str, Any]] = None,
) -> EntityNode:
    """T10 — register a real-world entity (person, property, office, ...)
    that edges and records can then be anchored to. Always a human/API
    action, not an automated pipeline step, so an actor is required
    unconditionally (same T08 rule as everything else that mutates and
    is audited)."""
    if actor_id is None:
        raise ValueError("creating a node requires an actor")
    if not entity_type or not entity_type.strip():
        raise ValueError("entity_type is required")
    if not label or not label.strip():
        raise ValueError("label is required")

    node = EntityNode(
        tenant_id=tenant_id,
        entity_type=entity_type.strip(),
        label=label.strip(),
        attributes=attributes or {},
    )
    db.add(node)
    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "entity_node.create",
        resource_type="entity_node", resource_id=node.id,
        details={"entity_type": node.entity_type, "label": node.label},
    )

    return node


async def find_similar_nodes(
    db: AsyncSession,
    tenant_id: UUID,
    entity_type: str,
    label: str,
    exclude_node_id: Optional[UUID] = None,
    threshold: Optional[float] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Entity-graph accuracy — the biggest real risk here isn't the tier/
    escrow logic (that's deterministic), it's silent fragmentation: OCR/
    handwriting variance reads the same real-world entity as different
    text on different pages ("Shri Juni Masjid, Hirpur" vs "Shri Juni
    Masjid, Village. Hirpur, Taluka. Murtizapur" — both seen for the same
    masjid in one real document this session), and create_node() has no
    way to know that. Never auto-merges anything — same "surface for
    operator resolution, do not silently discard/decide" contract as the
    document fuzzy-duplicate leg (duplicate_service.find_fuzzy_duplicates,
    T79), just for entity labels instead of document content. Scoped to
    the same entity_type: a person and a property sharing trigrams is
    meaningless, never a duplicate candidate.

    pg_trgm's similarity() (not word_similarity(), which finds the best-
    matching substring of a long text against a short query — T72's
    search-leg use case) is the right function for two short, comparable
    labels compared head-to-head.
    """
    if threshold is None:
        threshold = await get_float("entity_dedup_similarity_threshold", 0.45)

    stmt = text("""
        SELECT id, entity_type, label, similarity(:label, label) AS score
        FROM entity_dg_nodes
        WHERE tenant_id = :tenant_id
          AND entity_type = :entity_type
          AND id != COALESCE(:exclude_node_id, '00000000-0000-0000-0000-000000000000'::uuid)
          AND similarity(:label, label) >= :threshold
        ORDER BY score DESC
        LIMIT :limit
    """)
    res = await db.execute(stmt, {
        "label": label.strip(),
        "tenant_id": str(tenant_id),
        "entity_type": entity_type.strip(),
        "exclude_node_id": str(exclude_node_id) if exclude_node_id else None,
        "threshold": threshold,
        "limit": limit,
    })
    return [
        {"id": str(row.id), "entity_type": row.entity_type, "label": row.label, "similarity": round(float(row.score), 4)}
        for row in res.fetchall()
    ]


async def search_nodes(
    db: AsyncSession,
    tenant_id: UUID,
    query: str,
    limit: int = 20,
) -> list[EntityNode]:
    """Name-based lookup so a user can find a node's ID from a person/
    property name instead of needing the raw UUID already in hand — the
    Entity 360 page previously only accepted a pasted ID with no way to
    discover one from the UI."""
    stmt = (
        select(EntityNode)
        .where(EntityNode.tenant_id == tenant_id, EntityNode.label.ilike(f"%{query.strip()}%"))
        .order_by(EntityNode.label)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_edge(
    db: AsyncSession,
    tenant_id: UUID,
    edge_type: str,
    tier: int,
    source_node_id: UUID,
    target_type: str,
    target_node_id: Optional[UUID] = None,
    target_fact_id: Optional[UUID] = None,
    confidence: Optional[float] = None,
    evidence_fact_id: Optional[UUID] = None,
    created_by_actor_id: Optional[UUID] = None,
    created_by_policy_version: Optional[str] = None,
) -> EntityEdge:
    """T56 — create an edge with the tier's auto-commit/escrow policy applied.

    Status is never accepted as an input: tier decides it. Tier 1/2 land as
    'machine' immediately. Tier 3/4 always land as 'held', no matter how
    high `confidence` is or whether a human is doing the creating — "tier 4
    legal links human-only at any confidence" means confirmation is always
    a separate, later step, not something creation can shortcut.
    """
    if tier not in AUTO_COMMIT_TIERS | ESCROW_TIERS:
        raise ValueError(f"invalid tier {tier}; must be 1, 2, 3, or 4")
    if target_type not in ("entity", "fact"):
        raise ValueError(f"invalid target_type {target_type!r}; must be 'entity' or 'fact'")
    if created_by_actor_id is None and created_by_policy_version is None:
        raise ValueError("an edge must have a creating actor or policy version")

    # Real cross-tenant vulnerability found and fixed 2026-09-02 (D-2
    # security review): every ID below came straight from client input
    # with no ownership check, and entity_360_service's read side had no
    # tenant filter either -- a Tenant A user could link their own node to
    # a Tenant B fact/node ID and see its real content via Entity 360.
    # RLS does not catch this (it's currently inert -- see the review),
    # so this check is the only real protection.
    source_node = await db.get(EntityNode, source_node_id)
    if not source_node or source_node.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Source entity not found")

    if target_type == "entity":
        if target_node_id is None:
            raise ValueError("target_node_id is required when target_type is 'entity'")
        target_node = await db.get(EntityNode, target_node_id)
        if not target_node or target_node.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Target entity not found")
    else:
        if target_fact_id is None:
            raise ValueError("target_fact_id is required when target_type is 'fact'")
        target_fact = await db.get(Fact, target_fact_id)
        if not target_fact or target_fact.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Target fact not found")

    if evidence_fact_id is not None:
        evidence_fact = await db.get(Fact, evidence_fact_id)
        if not evidence_fact or evidence_fact.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Evidence fact not found")

    status = "machine" if tier in AUTO_COMMIT_TIERS else "held"

    edge = EntityEdge(
        tenant_id=tenant_id,
        edge_type=edge_type,
        tier=tier,
        source_node_id=source_node_id,
        target_type=target_type,
        target_node_id=target_node_id,
        target_fact_id=target_fact_id,
        confidence=confidence,
        status=status,
        created_by_actor_id=created_by_actor_id,
        created_by_policy_version=created_by_policy_version,
        evidence_fact_id=evidence_fact_id,
    )
    db.add(edge)
    await db.flush()

    if created_by_actor_id is not None:
        await log_action(
            db, created_by_actor_id, tenant_id, "entity_edge.create",
            resource_type="entity_edge", resource_id=edge.id,
            details={"edge_type": edge_type, "tier": tier, "status": status, "confidence": confidence},
        )

    return edge


async def delete_node(db: AsyncSession, tenant_id: UUID, node_id: UUID, actor_id: UUID) -> None:
    """Real gap found live 2026-09-09: create_node/create_edge were both
    exposed, but nothing ever let a caller remove a node created by
    mistake (a duplicate, a test fixture, ...) -- confirmed live: two
    verification-testing nodes had no way to be cleaned up via the API at
    all. Cascades to every edge referencing this node as source or target
    (entity_dg_edges.source_node_id / target_node_id are both
    ondelete='CASCADE') -- deliberate: an edge can't meaningfully survive
    the node it points at disappearing.
    """
    if actor_id is None:
        raise ValueError("deleting a node requires an actor")

    node = await db.get(EntityNode, node_id)
    if not node or node.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Entity not found")

    await log_action(
        db, actor_id, tenant_id, "entity_node.delete",
        resource_type="entity_node", resource_id=node.id,
        details={"entity_type": node.entity_type, "label": node.label},
    )

    await db.delete(node)
    await db.flush()


async def delete_edge(db: AsyncSession, tenant_id: UUID, edge_id: UUID, actor_id: UUID) -> None:
    """Same gap as delete_node, for a single edge -- e.g. a machine edge
    created from a bad match, without needing to delete either endpoint
    node just to remove it."""
    if actor_id is None:
        raise ValueError("deleting an edge requires an actor")

    edge = await db.get(EntityEdge, edge_id)
    if not edge or edge.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Edge not found")

    await log_action(
        db, actor_id, tenant_id, "entity_edge.delete",
        resource_type="entity_edge", resource_id=edge.id,
        details={"edge_type": edge.edge_type, "tier": edge.tier, "status": edge.status},
    )

    await db.delete(edge)
    await db.flush()


async def confirm_edge(db: AsyncSession, tenant_id: UUID, edge_id: UUID, actor_id: UUID) -> EntityEdge:
    """T56 — the single-edge human confirmation action: held -> verified.

    Tier 1/2 ('machine') edges are never promoted — "a link the machine
    created keeps that label for good, even after a person has looked at
    it" (Section 6). Only tier 3/4 'held' edges can be confirmed here.
    """
    if actor_id is None:
        raise ValueError("confirmation requires an actor")  # same rule as T08

    edge = await db.get(EntityEdge, edge_id)
    if not edge or edge.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Edge not found")

    if edge.status == "verified":
        raise HTTPException(status_code=409, detail="Edge is already verified")
    if edge.status != "held":
        raise HTTPException(
            status_code=409,
            detail=f"Tier {edge.tier} ('{edge.status}') edges do not go through confirmation — machine-accepted links stay permanently labelled",
        )

    edge.status = "verified"
    edge.verified_by_actor_id = actor_id
    edge.verified_at = datetime.utcnow()
    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "entity_edge.confirm",
        resource_type="entity_edge", resource_id=edge.id,
        details={"edge_type": edge.edge_type, "tier": edge.tier},
    )

    return edge


async def bulk_confirm_edges(
    db: AsyncSession,
    tenant_id: UUID,
    corpus_folder_id: UUID,
    threshold: float,
    actor_id: UUID,
    policy_version: str,
) -> dict:
    """T57 — bulk confirm is the same gate at scale: a person accepts every
    held edge above a chosen score for one collection, in one action.
    Record the user, the score, the collection and the rule version — on
    the log AND on every edge it touched (Section 6).

    "Corpus" is scoped to a folder, matching the existing container model
    (D-1). An edge is only reachable here through its evidence_fact_id ->
    document -> folder chain — an edge with no evidence (nullable per T10)
    can't be placed in any corpus and must go through confirm_edge()
    individually instead.

    Every confirmed edge gets a fresh verified_batch_id, the precise
    handle revert_bulk_batch() (T58) needs to undo exactly this run —
    policy_version is a reusable business label, not a unique run id.

    T59: bulk acceptance is disabled on a corpus until a human has
    calibrated it (corpus_calibration_service.calibrate_corpus) — a
    hardcoded/uncalibrated confidence score "implies calibrated
    confidence and carries none; any threshold built on it is
    meaningless" (scope gap, engineering standards).
    """
    if actor_id is None:
        raise ValueError("bulk confirmation requires an actor")
    if not policy_version:
        raise ValueError("bulk confirmation requires a policy/rule version")
    if threshold is None or not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be between 0 and 1")

    from app.services.corpus_calibration_service import is_corpus_calibrated
    if not await is_corpus_calibrated(db, tenant_id, corpus_folder_id):
        raise HTTPException(
            status_code=409,
            detail="This corpus has not been calibrated — bulk acceptance is disabled until a human certifies "
                   "the confidence scores here are meaningful (corpus_calibration_service.calibrate_corpus)",
        )

    stmt = (
        select(EntityEdge)
        .join(Fact, EntityEdge.evidence_fact_id == Fact.id)
        .join(Document, Fact.document_id == Document.id)
        .where(
            EntityEdge.tenant_id == tenant_id,
            EntityEdge.status == "held",
            EntityEdge.confidence.is_not(None),
            EntityEdge.confidence >= threshold,
            Document.folder_id == corpus_folder_id,
        )
    )
    res = await db.execute(stmt)
    edges = list(res.scalars().all())

    batch_id = uuid_module.uuid4()
    now = datetime.utcnow()
    for edge in edges:
        edge.status = "verified"
        edge.verified_by_actor_id = actor_id
        edge.verified_at = now
        edge.verified_threshold = threshold
        edge.verified_corpus_folder_id = corpus_folder_id
        edge.verified_via_policy_version = policy_version
        edge.verified_batch_id = batch_id

    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "entity_edge.bulk_confirm",
        resource_type="folder", resource_id=corpus_folder_id,
        details={
            "batch_id": str(batch_id),
            "threshold": threshold,
            "policy_version": policy_version,
            "confirmed_count": len(edges),
            "edge_ids": [str(e.id) for e in edges],
        },
    )

    return {"batch_id": batch_id, "confirmed_count": len(edges), "edge_ids": [e.id for e in edges]}


async def revert_edge(db: AsyncSession, tenant_id: UUID, edge_id: UUID, actor_id: UUID) -> EntityEdge:
    """T58 — link reversibility: undo one confirmation, verified -> held.

    "A link the machine created keeps that label for good, even after a
    person has looked at it" — machine (tier 1/2) edges can never be
    reverted because they were never a human decision to begin with.
    Reverting clears every verified_* field, whether the edge was
    confirmed individually or as part of a bulk batch — the *history* of
    who verified it and when lives in the append-only audit log, not on
    the live edge row.
    """
    if actor_id is None:
        raise ValueError("reverting a confirmation requires an actor")

    edge = await db.get(EntityEdge, edge_id)
    if not edge or edge.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Edge not found")

    if edge.status == "machine":
        raise HTTPException(
            status_code=409,
            detail="Machine-accepted links stay permanently labelled and cannot be reverted",
        )
    if edge.status == "held":
        raise HTTPException(status_code=409, detail="Edge is not verified — nothing to revert")

    previous_verifier = edge.verified_by_actor_id
    previous_batch_id = edge.verified_batch_id

    edge.status = "held"
    edge.verified_by_actor_id = None
    edge.verified_at = None
    edge.verified_threshold = None
    edge.verified_corpus_folder_id = None
    edge.verified_via_policy_version = None
    edge.verified_batch_id = None
    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "entity_edge.revert",
        resource_type="entity_edge", resource_id=edge.id,
        details={
            "edge_type": edge.edge_type,
            "tier": edge.tier,
            "previously_verified_by": str(previous_verifier) if previous_verifier else None,
            "previous_batch_id": str(previous_batch_id) if previous_batch_id else None,
        },
    )

    return edge


async def revert_bulk_batch(db: AsyncSession, tenant_id: UUID, batch_id: UUID, actor_id: UUID) -> dict:
    """T58 — undo an entire bulk-confirm run in one action, by its exact
    batch_id. Clean cascade: reverts precisely the edges that batch
    touched, nothing from any other run, back to 'held'.
    """
    if actor_id is None:
        raise ValueError("reverting a bulk confirmation requires an actor")

    stmt = select(EntityEdge).where(
        EntityEdge.tenant_id == tenant_id,
        EntityEdge.verified_batch_id == batch_id,
        EntityEdge.status == "verified",
    )
    res = await db.execute(stmt)
    edges = list(res.scalars().all())

    for edge in edges:
        edge.status = "held"
        edge.verified_by_actor_id = None
        edge.verified_at = None
        edge.verified_threshold = None
        edge.verified_corpus_folder_id = None
        edge.verified_via_policy_version = None
        edge.verified_batch_id = None

    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "entity_edge.bulk_revert",
        resource_type="entity_edge_batch", resource_id=batch_id,
        details={"reverted_count": len(edges), "edge_ids": [str(e.id) for e in edges]},
    )

    return {"reverted_count": len(edges), "edge_ids": [e.id for e in edges]}


# --- Auto-extraction from document facts (2026-09-10, QA report #5) ---
#
# Entity 360 always returned empty for every real document, because
# nothing ever called create_node/create_edge outside the API router and
# tests — confirmed live: check_entity_graph.py's own docstring says as
# much, and the only entity_dg_nodes/entity_dg_edges rows in the real
# tenant were hand-created demo/healthcheck data, completely disconnected
# from the corpus's 3,240 real facts. This is the first automated caller,
# invoked from worker.py right after VLM fact extraction (T22) writes
# doc_dg_facts for a document.
#
# Deliberately narrow, not a general entity-extraction engine: template
# field_schema carries no semantic role today (T22's field_schema.role is
# structural — serial/continuation_text/chain_anchor/page_header only), so
# there is no reliable signal for "this field names a person" beyond the
# field's own name. Matching is a fixed keyword list built from what this
# corpus's real registered templates actually use (wakf_gazette_form_a,
# wardha_form_b) to name a person or the property/institution itself —
# survey numbers, villages, valuations etc. are attributes of a record, not
# distinct real-world entities worth a graph node on their own. A template
# using different field-naming conventions won't be picked up by this pass;
# treat it as a first pass, not a solved problem.
AUTO_EXTRACT_POLICY_VERSION = "auto-facts-v1"

_PERSON_FIELD_NAMES = {"mutawalli_name", "owner_name", "applicant_name", "witness_name", "name"}
_PROPERTY_FIELD_NAMES = {"wakf_name"}


def _classify_fact_field(field_name: str) -> Optional[str]:
    if field_name in _PERSON_FIELD_NAMES:
        return "person"
    if field_name in _PROPERTY_FIELD_NAMES:
        return "property"
    return None


# Relationship inference (2026-09-23). Entity 360's "Linked entities" panel
# was 0 for every entity in the system: auto_extract_entities_from_facts only
# ever writes entity->FACT edges, so nothing had ever produced an entity->
# entity edge outside manual API calls (4 of 1,910 live edges, all hand-made).
RELATIONSHIP_POLICY_VERSION = "auto-relationships-v1"

# A person field and a property field sharing a Fact.row_group_id came off the
# SAME physical table row -- row identity recorded at extraction time, not
# re-derived from geometry (see Fact.row_group_id). In a Waqf register that
# pairing is a manages-relationship. Row-scoping is also what keeps this pass
# from repeating the over-linking already visible elsewhere in the graph,
# where an entity is linked to every same-named fact in its whole document.
RELATIONSHIP_EDGE_TYPE = "manages"

# Ditto/continuation placeholders. Live count, demo tenant: of 778 row groups
# holding both a person and a property field, 348 carry one of these on one or
# both sides. Linking them would create entities literally named "..".
_PLACEHOLDER_LABELS = {
    "", "do", "ditto", "same", "same as above", "nil", "na", "n/a", "none", "-",
    # Real strings in the registers that are not real entities. "Not available"
    # became a person node with 10 relationships on the first live run.
    "not available", "not known", "unknown", "no", "yes", "nn", "xx",
}
# Latin or Devanagari -- a label must carry a real word, not just digits,
# punctuation or a stray glyph off a bad scan.
_MEANINGFUL_LABEL_RE = re.compile(r"[A-Za-z\u0900-\u097F]{3,}")


def _is_meaningful_label(label: Optional[str]) -> bool:
    """A label worth building a legal-ish relationship on."""
    raw = (label or "").strip()
    if not raw:
        return False
    if raw.lower().strip(" .-\u2013\u2014") in _PLACEHOLDER_LABELS:
        return False
    return bool(_MEANINGFUL_LABEL_RE.search(raw))


async def auto_extract_relationships_from_facts(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID,
) -> int:
    """Infer entity->entity relationships from facts sharing a row_group_id.

    Best-effort, same contract as auto_extract_entities_from_facts: never
    raises on a per-row failure, returns 0 rather than inventing an actor.

    Deliberately tier 2, not tier 3. A relationship read off one physical
    table row is a mechanical observation of the same evidential class as
    "mentioned_in" -- it auto-commits as 'machine' and stays permanently
    labelled machine-made. Tier 3 escrow stays reserved for identity claims
    ("these two records are the same legal person"), which are a different
    question and which this pass deliberately does not attempt.

    Idempotent: an existing edge of the same type between the same two nodes
    is left alone, so re-ingest and backfill can both run safely.

    Returns the number of new relationship edges created this call."""
    stmt = select(Fact).where(
        Fact.tenant_id == tenant_id,
        Fact.document_id == document_id,
        Fact.version_id == version_id,
        Fact.row_group_id.isnot(None),
        Fact.field_name.in_(_PERSON_FIELD_NAMES | _PROPERTY_FIELD_NAMES),
    )
    res = await db.execute(stmt)
    facts = list(res.scalars().all())
    if not facts:
        return 0

    from app.services.document_service import _resolve_policy_actor
    actor_id = await _resolve_policy_actor(db, tenant_id, {})
    if actor_id is None:
        return 0

    # One row -> at most one relationship. Where a row somehow carries more
    # than one person or property field, take the first meaningful one rather
    # than emitting a cross product.
    rows: Dict[Any, Dict[str, Any]] = {}
    for fact in facts:
        entity_type = _classify_fact_field(fact.field_name)
        if entity_type is None:
            continue
        raw_value = fact.value.get("v") if isinstance(fact.value, dict) else None
        label = str(raw_value).strip() if raw_value else ""
        if not _is_meaningful_label(label):
            continue
        slot = rows.setdefault(fact.row_group_id, {})
        if entity_type not in slot:
            slot[entity_type] = (label, fact)

    edges_created = 0
    nodes_created = 0
    for row_group_id, slot in rows.items():
        if "person" not in slot or "property" not in slot:
            continue
        person_label, person_fact = slot["person"]
        property_label, property_fact = slot["property"]

        node_ids = {}
        try:
            for entity_type, label in (("person", person_label), ("property", property_label)):
                similar = await find_similar_nodes(db, tenant_id, entity_type, label)
                if similar:
                    node_ids[entity_type] = UUID(similar[0]["id"])
                else:
                    node = await create_node(
                        db, tenant_id, entity_type, label, actor_id=actor_id,
                        attributes={
                            "auto_extracted": True,
                            "extraction_policy_version": RELATIONSHIP_POLICY_VERSION,
                        },
                    )
                    node_ids[entity_type] = node.id
                    nodes_created += 1
        except HTTPException:
            continue

        src, dst = node_ids["person"], node_ids["property"]
        if src == dst:
            # pg_trgm collapsed both sides onto one node (a row where the
            # manager and the property carry near-identical text, common on a
            # bad scan). A self-edge is meaningless -- skip rather than store.
            continue

        existing = await db.execute(
            select(EntityEdge).where(
                EntityEdge.tenant_id == tenant_id,
                EntityEdge.source_node_id == src,
                EntityEdge.target_node_id == dst,
                EntityEdge.edge_type == RELATIONSHIP_EDGE_TYPE,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            continue

        try:
            await create_edge(
                db, tenant_id, edge_type=RELATIONSHIP_EDGE_TYPE, tier=2,
                source_node_id=src, target_type="entity", target_node_id=dst,
                confidence=min(
                    person_fact.confidence if person_fact.confidence is not None else 1.0,
                    property_fact.confidence if property_fact.confidence is not None else 1.0,
                ),
                evidence_fact_id=property_fact.id,
                created_by_policy_version=RELATIONSHIP_POLICY_VERSION,
            )
            edges_created += 1
        except HTTPException:
            continue

    if edges_created or nodes_created:
        await log_action(
            db, actor_id, tenant_id, "entity_graph.auto_relationships",
            resource_type="document", resource_id=document_id,
            details={
                "edges_created": edges_created,
                "nodes_created": nodes_created,
                "policy_version": RELATIONSHIP_POLICY_VERSION,
            },
        )

    return edges_created


async def auto_extract_entities_from_facts(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID,
) -> int:
    """Best-effort — called from the ingestion pipeline right after VLM fact
    extraction, wrapped by the caller in its own savepoint/try-except the
    same way every other optional ingestion stage is (Section 3.5: search
    must never wait on this). Never raises on a per-fact failure; skips
    entirely (returns 0) if this tenant somehow has no user to attribute
    creation to, rather than inventing one.

    Each matching fact becomes a tier-2 "mentioned_in" edge (auto-commits
    as 'machine' — a mechanical mention is low-risk, Section 6) from a
    person/property node to the fact itself, so Entity 360 has something
    real to show. Nodes are deduped via find_similar_nodes (pg_trgm) before
    creating a new one — the same "surface for a human, never silently
    merge or fragment" contract every other entity-graph write already
    follows — so repeated mentions of the same name across many facts
    collapse onto one node instead of creating a duplicate per fact.

    Returns the number of new EntityNode rows created this call."""
    stmt = select(Fact).where(
        Fact.tenant_id == tenant_id,
        Fact.document_id == document_id,
        Fact.version_id == version_id,
        Fact.field_name.in_(_PERSON_FIELD_NAMES | _PROPERTY_FIELD_NAMES),
    )
    res = await db.execute(stmt)
    facts = list(res.scalars().all())
    if not facts:
        return 0

    # T66's exact pattern for a policy-driven action that still needs a
    # real, FK'able actor to attribute audit events to (audit_dg_logs.
    # actor_id is NOT NULL at the DB level) — see document_service.py's
    # _resolve_policy_actor docstring.
    from app.services.document_service import _resolve_policy_actor
    actor_id = await _resolve_policy_actor(db, tenant_id, {})
    if actor_id is None:
        return 0

    nodes_created = 0
    edges_created = 0
    for fact in facts:
        entity_type = _classify_fact_field(fact.field_name)
        if entity_type is None:
            continue
        raw_value = fact.value.get("v") if isinstance(fact.value, dict) else None
        label = str(raw_value).strip() if raw_value else ""
        if not label:
            continue

        similar = await find_similar_nodes(db, tenant_id, entity_type, label)
        if similar:
            node_id = UUID(similar[0]["id"])
        else:
            node = await create_node(
                db, tenant_id, entity_type, label, actor_id=actor_id,
                attributes={"auto_extracted": True, "extraction_policy_version": AUTO_EXTRACT_POLICY_VERSION},
            )
            node_id = node.id
            nodes_created += 1

        try:
            await create_edge(
                db, tenant_id, edge_type="mentioned_in", tier=2,
                source_node_id=node_id, target_type="fact", target_fact_id=fact.id,
                confidence=fact.confidence, evidence_fact_id=fact.id,
                created_by_policy_version=AUTO_EXTRACT_POLICY_VERSION,
            )
            edges_created += 1
        except HTTPException:
            # Fact or node failed a tenant-ownership check mid-loop —
            # skip this one mention, keep processing the rest.
            continue

    if nodes_created or edges_created:
        await log_action(
            db, actor_id, tenant_id, "entity_graph.auto_extract",
            resource_type="document", resource_id=document_id,
            details={
                "nodes_created": nodes_created,
                "edges_created": edges_created,
                "policy_version": AUTO_EXTRACT_POLICY_VERSION,
            },
        )

    return nodes_created
