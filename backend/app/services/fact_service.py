from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fact import Fact
from app.models.page import DocumentPage
from app.models.document import Document
from app.models.template import Template
from app.services.storage_service import generate_presigned_url

# Row-reconstruction tolerance for get_table_view_for_document: two facts
# whose anchor regions land on the same page within this many page-height
# fractions of each other are treated as the same original table row.
# Chosen against this project's real 14-column Wakf Form B table (~15
# rows per landscape page, each row occupying roughly 0.05-0.07 of page
# height) -- half a row-height leaves enough margin to absorb ordinary
# per-cell y-jitter within one row without bleeding into the row below.
_ROW_CLUSTER_Y_TOLERANCE = 0.02


async def get_fact_with_regions(db: AsyncSession, fact_id: UUID, tenant_id: UUID) -> dict:
    """T53 — everything a click-through viewer needs for one fact: its
    regions, each region's page (for rotation/skew/width/height per T06),
    and the source document's presigned URL to render.
    """
    stmt = (
        select(Fact)
        .where(Fact.id == fact_id, Fact.tenant_id == tenant_id)
        .options(selectinload(Fact.regions))
    )
    res = await db.execute(stmt)
    fact = res.scalar_one_or_none()
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")

    doc_res = await db.execute(
        select(Document)
        .where(Document.id == fact.document_id, Document.tenant_id == tenant_id)
        .options(selectinload(Document.versions))
    )
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Source document not found")

    page_ids = {r.page_id for r in fact.regions}
    pages_res = await db.execute(select(DocumentPage).where(DocumentPage.id.in_(page_ids)))
    pages_by_id = {p.id: p for p in pages_res.scalars().all()}

    curr_version = next((v for v in doc.versions if v.id == doc.current_version_id), None)
    download_url = None
    if curr_version and curr_version.s3_path:
        download_url = await generate_presigned_url(curr_version.s3_path)

    regions_out = []
    for region in fact.regions:
        page = pages_by_id.get(region.page_id)
        if not page:
            continue
        regions_out.append({
            "region_id": str(region.id),
            "page_number": page.page_number,
            "page_width": page.width,
            "page_height": page.height,
            "rotation": page.rotation,
            "skew": page.skew,
            "x0": region.x0,
            "y0": region.y0,
            "x1": region.x1,
            "y1": region.y1,
        })

    return {
        "fact_id": str(fact.id),
        "field_name": fact.field_name,
        "value": fact.value,
        "confidence": fact.confidence,
        "edit_version": fact.edit_version,
        "status": fact.status,
        "is_handwritten": fact.is_handwritten,
        "document_id": str(fact.document_id),
        "document_title": doc.title,
        "download_url": download_url,
        "regions": regions_out,
    }


async def create_fact_with_regions(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID,
    field_name: str,
    value: dict | list | str | float | int,
    regions: list[dict],
    confidence: Optional[float] = None,
    is_handwritten: bool = False,
    status: str = "in_review",
) -> Fact:
    """Enforce Artifact Section 2 / T04 rule: 'If a fact has no region, refuse to save it.'

    Every fact MUST point at the exact spot on the page it came from before being persisted.
    """
    from app.models.fact_region import FactRegion
    from app.utils.bbox import normalize_bbox

    if not regions:
        raise ValueError("Cannot save fact: 'no region, no save' rule requires at least one FactRegion attached.")

    valid_regions = []
    for r in regions:
        bbox = [r.get("x0"), r.get("y0"), r.get("x1"), r.get("y1")]
        norm = normalize_bbox(bbox, r.get("page_width"), r.get("page_height"))
        if norm and r.get("page_id"):
            valid_regions.append((r["page_id"], norm))

    if not valid_regions:
        raise ValueError("Cannot save fact: 'no region, no save' rule requires at least one valid FactRegion attached.")

    fact = Fact(
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=version_id,
        field_name=field_name,
        value=value if isinstance(value, (dict, list)) else {"v": value},
        confidence=confidence,
        is_handwritten=is_handwritten,
        status=status,
    )
    db.add(fact)
    await db.flush()

    for page_id, norm_bbox in valid_regions:
        x0, y0, x1, y1 = norm_bbox
        db.add(
            FactRegion(
                tenant_id=tenant_id,
                fact_id=fact.id,
                page_id=page_id,
                x0=float(x0),
                y0=float(y0),
                x1=float(x1),
                y1=float(y1),
            )
        )

    await db.flush()
    return fact


async def get_facts_for_document(db: AsyncSession, document_id: UUID, tenant_id: UUID) -> dict:
    """List every extracted field for one document, for the "what did
    extraction actually produce" view no frontend screen currently shows
    (found live 2026-09-04: opening a document's preview has no facts
    panel at all -- the only places Facts ever surfaced were the
    in-review-only adjudication queue and Entity 360's per-entity search,
    neither of which lets you just look at one document).

    `stitched` on a fact is TS1's own signal, not a heuristic re-derived
    here: a fact whose regions land on more than one physical page can
    only exist because _stitch_vertical_segments merged a continuation
    row from a later page into the entry it belongs to -- that is the
    literal, verifiable proof stitching happened for that field, the same
    check used to confirm it live before this endpoint existed.
    """
    doc = await db.get(Document, document_id)
    if not doc or doc.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    stmt = (
        select(Fact)
        .where(Fact.document_id == document_id, Fact.tenant_id == tenant_id)
        .options(selectinload(Fact.regions))
        .order_by(Fact.field_name)
    )
    res = await db.execute(stmt)
    facts = res.scalars().all()

    page_ids = {region.page_id for fact in facts for region in fact.regions}
    pages_res = await db.execute(select(DocumentPage).where(DocumentPage.id.in_(page_ids)))
    page_number_by_id = {p.id: p.page_number for p in pages_res.scalars().all()}

    facts_out = []
    for fact in facts:
        page_numbers = sorted({
            page_number_by_id[r.page_id] for r in fact.regions if r.page_id in page_number_by_id
        })
        facts_out.append({
            "fact_id": str(fact.id),
            "field_name": fact.field_name,
            "value": fact.value,
            "confidence": fact.confidence,
            "edit_version": fact.edit_version,
            "status": fact.status,
            "is_handwritten": fact.is_handwritten,
            "page_numbers": page_numbers,
            "stitched": len(page_numbers) > 1,
        })

    # Found live 2026-09-04: a document can easily have hundreds of facts
    # (this exact one has 788) but only a handful are ever stitched or
    # in_review -- sorted by plain field_name, those few signal-carrying
    # rows were buried at random scroll positions among hundreds of
    # identical-looking ones, making a real, working stitch look like
    # nothing had happened. Surface in_review first (needs a decision),
    # then stitched (the interesting proof), then everything else.
    facts_out.sort(key=lambda f: (f["status"] != "in_review", not f["stitched"], f["field_name"]))

    return {
        "document_id": str(document_id),
        "classification_status": doc.classification_status,
        "matched_template_id": str(doc.matched_template_id) if doc.matched_template_id else None,
        "facts": facts_out,
        "page_count": doc.pages_total_count or 0,
        "stitched_field_count": sum(1 for f in facts_out if f["stitched"]),
        "in_review_count": sum(1 for f in facts_out if f["status"] == "in_review"),
    }


def _unwrap_fact_value(value):
    if isinstance(value, dict) and "v" in value:
        return value["v"]
    return value


def cluster_fact_rows(facts: list, page_number_by_id: dict, header_field_names: set) -> tuple[dict, list[dict]]:
    """Groups a document's facts into table rows -- the row-identity logic
    get_table_view_for_document's docstring describes, shared with the
    review screen so both always agree on what a row is. Returns
    ({header_field_name: first Fact}, [{"page_number", "anchor_y", "facts"}])
    with rows in (page, y) order."""
    header_facts: dict = {}
    row_items: list[tuple[Fact, int, float]] = []  # (fact, anchor_page_number, anchor_y0)

    for fact in facts:
        if fact.field_name in header_field_names:
            if fact.field_name not in header_facts:
                header_facts[fact.field_name] = fact
            continue

        anchor = None
        for r in fact.regions:
            page_number = page_number_by_id.get(r.page_id)
            if page_number is None:
                continue
            if anchor is None or (page_number, r.y0) < (anchor[0], anchor[1]):
                anchor = (page_number, r.y0)
        if anchor is None:
            continue
        row_items.append((fact, anchor[0], anchor[1]))

    row_items.sort(key=lambda t: (t[1], t[2]))

    # Exact groups first: real row identity, no guessing. Facts sharing a
    # row_group_id always form one row regardless of how far apart their
    # regions land on the page (see Fact.row_group_id).
    exact_groups: dict = {}
    heuristic_items: list[tuple[Fact, int, float]] = []
    for fact, page_number, y0 in row_items:
        if fact.row_group_id is not None:
            group = exact_groups.get(fact.row_group_id)
            if group is None:
                group = {"page_number": page_number, "anchor_y": y0, "facts": []}
                exact_groups[fact.row_group_id] = group
            group["facts"].append(fact)
            if (page_number, y0) < (group["page_number"], group["anchor_y"]):
                group["page_number"], group["anchor_y"] = page_number, y0
        else:
            heuristic_items.append((fact, page_number, y0))

    # Fallback for facts with no stored row identity (extracted before
    # row_group_id existed) — old (page, y0)-proximity clustering,
    # unchanged. heuristic_items is still in the (page, y0) sort order
    # from row_items above, which this loop depends on.
    heuristic_clusters: list[dict] = []
    for fact, page_number, y0 in heuristic_items:
        if (
            heuristic_clusters
            and heuristic_clusters[-1]["page_number"] == page_number
            and abs(heuristic_clusters[-1]["anchor_y"] - y0) <= _ROW_CLUSTER_Y_TOLERANCE
        ):
            heuristic_clusters[-1]["facts"].append(fact)
        else:
            heuristic_clusters.append({"page_number": page_number, "anchor_y": y0, "facts": [fact]})

    clusters = list(exact_groups.values()) + heuristic_clusters
    clusters.sort(key=lambda c: (c["page_number"], c["anchor_y"]))
    return header_facts, clusters


async def get_table_view_for_document(db: AsyncSession, document_id: UUID, tenant_id: UUID) -> dict:
    """Reassembles this document's facts into an actual table — rows x
    columns, in the template's own column order — instead of the flat
    per-field list get_facts_for_document returns. Found live 2026-09-04:
    a flat list of ~788 individual field cards reads as "nothing
    happened" even when extraction and stitching both worked correctly,
    because it looks nothing like the source document's table structure a
    reviewer actually recognizes.

    Groups by Fact.row_group_id when set -- the real row identity
    vlm_extraction.py already knows at write time (one merged_row / one
    result.pairs entry), stored since the row-grouping bug fix below.
    Only facts with no row_group_id (extracted before that column
    existed) fall back to the OLD heuristic: clustering by FactRegion's
    (page, y0) with a fixed tolerance, on the assumption that fields from
    the same original row land at nearly the same vertical position.

    Real bug found live 2026-09-07 (Wardha.pdf): that heuristic has no
    x0 disambiguation at all, so it breaks badly on any page laid out as
    side-by-side entry-columns rather than left-to-right rows -- one
    entry's own fields span nearly the full page height there (shattering
    into many near-empty "rows"), while unrelated entries' same-named
    fields land on the same y-band and get wrongly merged into one row,
    silently overwriting all but one (see `row[fact.field_name] = value`
    below -- 7 of every 8 real entries lost on the affected page). The
    heuristic is kept, unchanged, only as a fallback for documents that
    can never be re-extracted to gain real row_group_id data -- new
    extractions no longer depend on it. A stitched fact (regions on 2+
    pages) is anchored by its EARLIEST region either way -- the page/row
    its entry actually started on.

    get_facts_for_document remains the authoritative per-fact source
    (used for editing/confirming) regardless of which path a row took
    here.
    """
    doc = await db.get(Document, document_id)
    if not doc or doc.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    field_order: list[str] = []
    header_field_names: set[str] = set()
    if doc.matched_template_id:
        template = await db.get(Template, doc.matched_template_id)
        if template:
            for f in template.field_schema:
                if f.get("role") == "page_header":
                    header_field_names.add(f["name"])
                else:
                    field_order.append(f["name"])

    stmt = (
        select(Fact)
        .where(Fact.document_id == document_id, Fact.tenant_id == tenant_id)
        .options(selectinload(Fact.regions))
    )
    res = await db.execute(stmt)
    facts = [f for f in res.scalars().all() if not f.field_name.startswith("_")]

    page_ids = {region.page_id for fact in facts for region in fact.regions}
    pages_res = await db.execute(select(DocumentPage).where(DocumentPage.id.in_(page_ids)))
    page_number_by_id = {p.id: p.page_number for p in pages_res.scalars().all()}

    header_facts, clusters = cluster_fact_rows(facts, page_number_by_id, header_field_names)
    page_header: dict = {name: _unwrap_fact_value(f.value) for name, f in header_facts.items()}

    rows_out = []
    for cluster in clusters:
        row: dict = {}
        # Which fact each column came from, so a client can show exactly
        # that value on the scan (Drive preview: click a cell -> outlined).
        row_fact_ids: dict = {}
        row_stitched = False
        row_needs_review = False
        for fact in cluster["facts"]:
            row[fact.field_name] = _unwrap_fact_value(fact.value)
            row_fact_ids[fact.field_name] = str(fact.id)
            if len({r.page_id for r in fact.regions}) > 1:
                row_stitched = True
            if fact.status == "in_review":
                row_needs_review = True
        rows_out.append({
            "page_number": cluster["page_number"],
            "stitched": row_stitched,
            # T22's row-coverage gate (ROW_COVERAGE_REVIEW_THRESHOLD)
            # forces every field of a badly-under-populated row into
            # in_review at write time -- surfaced here so a row that's
            # probably mis-mapped page content (not a real table row)
            # is visibly flagged, not indistinguishable from a normal one.
            "needs_review": row_needs_review,
            "values": row,
            "fact_ids": row_fact_ids,
        })

    # Any field seen but absent from the template's own schema (e.g. a
    # template with no field_schema on record, or a field the template
    # was updated to drop after this document was extracted) still gets
    # a column, appended after the schema's own order, so no data is
    # silently dropped from the reconstructed table.
    seen_fields = {name for row in rows_out for name in row["values"].keys()}
    columns = field_order + sorted(seen_fields - set(field_order))

    return {
        "document_id": str(document_id),
        "classification_status": doc.classification_status,
        "page_header": page_header,
        "columns": columns,
        "rows": rows_out,
        "row_count": len(rows_out),
        "page_count": doc.pages_total_count or 0,
    }
