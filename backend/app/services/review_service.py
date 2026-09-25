"""Review screen (human verification) -- scan on one side, extracted blocks
on the other, every correction reversible and audited.

Three layers, and which one owns what:

  doc_dg_review_originals  immutable snapshot of the blocks exactly as
                           extraction produced them (per document version).
  doc_dg_facts             the single source of truth for every fact-backed
                           table cell. Review edits go through the same
                           fact edit path as the Workbench
                           (fact_verification_service.bulk_edit_facts), so
                           an edit always lands 'in_review', never
                           'verified'.
  doc_dg_review_states     everything that is NOT a fact: block/row order,
                           reviewer-added text blocks and rows, soft
                           deletions, and verification attestations. A
                           fact-backed cell is stored here as {"fact_id"}
                           plus the fact version the review screen itself
                           last wrote ("rv").

Concurrency: every write names the review-state version it was made
against (If-Match) and, for a fact cell, the fact's edit_version. Either
being stale is a 409 -- the Workbench shares the fact half of that check.

A single-cell revert is only allowed while the fact still holds the value
this screen wrote (fact.edit_version == rv). If anyone changed it
elsewhere since, the revert is refused rather than clobbering their change.

Verification vs. D-5: a 'machine' fact is never promoted to 'verified'
(T51 -- auto-committed values keep that label). Verifying a row therefore
confirms the row's in_review facts through confirm_fact() and records a
row-level attestation (who, when, and the fact versions attested). The
attestation silently lapses the moment any of those facts changes.
"""
import hashlib
import json
import uuid as uuid_module
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, event, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.entity_edge import EntityEdge
from app.models.fact import Fact, bump_edit_version, check_edit_version
from app.models.page import DocumentPage
from app.models.review import ReviewAuditEntry, ReviewOriginal, ReviewState
from app.models.template import Template
from app.services import fact_verification_service
from app.permissions import grants
from app.services.audit_service import log_action
from app.services.config_service import get_float
from app.services.fact_service import cluster_fact_rows

MACHINE = "MACHINE_EXTRACTED"
EDITED = "EDITED"
VERIFIED = "VERIFIED"

# Custom roles: permission keys (app/permissions.py). `role` parameters
# below take the caller's live access (TokenPayload) or a persona string.
READ_PERMISSION = "review.read"
EDIT_PERMISSION = "review.edit"
VERIFY_PERMISSION = "review.verify"
REVERT_ALL_PERMISSION = "review.revertAll"

TEXT_BLOCK_TYPES = ("heading", "paragraph")
REMATCH_POLICY_VERSION = "review-edit-rematch-v1"

# A region covering (almost) the whole page is a placeholder, not a location
# -- spread and stitch-ambiguity facts are written with 0,0,1,1 because no
# real cell position exists (vlm_extraction.py). Drawing it would outline
# the entire scan and mislead the reviewer.
_PAGE_LEVEL_AREA = 0.95


def permissions_for(role: Any) -> Dict[str, bool]:
    return {
        "can_edit": grants(role, EDIT_PERMISSION),
        "can_verify": grants(role, VERIFY_PERMISSION),
        "can_revert_all": grants(role, REVERT_ALL_PERMISSION),
    }


# --------------------------------------------------------------------------
# Building the immutable original from facts
# --------------------------------------------------------------------------

def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "v" in value:
        return value["v"]
    return value


def _as_text(value: Any) -> str:
    v = _unwrap(value)
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _region_box(region, page_number: int) -> Optional[Dict[str, float]]:
    w = max(0.0, region.x1 - region.x0)
    h = max(0.0, region.y1 - region.y0)
    if w * h >= _PAGE_LEVEL_AREA:
        return None
    return {"page": page_number, "x": region.x0, "y": region.y0, "w": w, "h": h}


def _union(boxes: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


async def _pre_edit_values(db: AsyncSession, tenant_id: UUID, fact_ids: List[UUID]) -> Dict[UUID, Dict[str, Any]]:
    """The value/status each fact had before its FIRST recorded edit.

    A document may have been corrected in the Workbench before anyone opened
    the review screen; the snapshot must still hold the machine's reading,
    not the corrected one. bulk_edit_facts writes previous_value/status on
    every fact.bulk_edit audit row -- the earliest one is the OCR original."""
    if not fact_ids:
        return {}
    res = await db.execute(
        select(AuditLog.resource_id, AuditLog.details)
        .where(
            AuditLog.actor_tenant_id == tenant_id,
            AuditLog.action == "fact.bulk_edit",
            AuditLog.resource_id.in_(fact_ids),
        )
        .order_by(AuditLog.created_at.asc())
    )
    first: Dict[UUID, Dict[str, Any]] = {}
    for resource_id, details in res.all():
        if resource_id not in first and isinstance(details, dict) and "previous_value" in details:
            first[resource_id] = {"value": details["previous_value"], "status": details.get("previous_status")}
    return first


def _fact_cell(fact: Fact, page_number_by_id: Dict[UUID, int], pre_edit: Dict[UUID, Dict[str, Any]]) -> Dict[str, Any]:
    regions = []
    for r in sorted(fact.regions, key=lambda r: (page_number_by_id.get(r.page_id, 0), r.y0)):
        page_number = page_number_by_id.get(r.page_id)
        if page_number is None:
            continue
        box = _region_box(r, page_number)
        if box:
            regions.append(box)
    original = pre_edit.get(fact.id)
    original_value = original["value"] if original else fact.value
    original_status = (original or {}).get("status") or fact.status
    return {
        "fact_id": str(fact.id),
        "field_name": fact.field_name,
        "text": _as_text(original_value),
        "value_json": original_value,
        "status": original_status,
        "confidence": fact.confidence,
        "bbox": regions[0] if regions else None,
        "regions": regions,
    }


def _empty_cell() -> Dict[str, Any]:
    return {"fact_id": None, "text": "", "confidence": None, "bbox": None, "regions": []}


async def _build_blocks_from_facts(db: AsyncSession, doc: Document) -> List[Dict[str, Any]]:
    field_order: List[str] = []
    header_names: List[str] = []
    if doc.matched_template_id:
        template = await db.get(Template, doc.matched_template_id)
        for f in (template.field_schema if template else []) or []:
            if f.get("role") == "page_header":
                header_names.append(f["name"])
            else:
                field_order.append(f["name"])

    res = await db.execute(
        select(Fact)
        .where(Fact.document_id == doc.id, Fact.tenant_id == doc.tenant_id)
        .options(selectinload(Fact.regions))
    )
    facts = [f for f in res.scalars().all() if not f.field_name.startswith("_")]
    if not facts:
        return []

    page_ids = {r.page_id for f in facts for r in f.regions}
    pages_res = await db.execute(select(DocumentPage).where(DocumentPage.id.in_(page_ids)))
    page_number_by_id = {p.id: p.page_number for p in pages_res.scalars().all()}
    pre_edit = await _pre_edit_values(db, doc.tenant_id, [f.id for f in facts])

    header_facts, clusters = cluster_fact_rows(facts, page_number_by_id, set(header_names))
    blocks: List[Dict[str, Any]] = []

    if header_facts:
        names = [n for n in header_names if n in header_facts] + sorted(set(header_facts) - set(header_names))
        cells = [_fact_cell(header_facts[n], page_number_by_id, pre_edit) for n in names]
        boxes = [c["bbox"] for c in cells if c["bbox"]]
        pages = sorted({b["page"] for b in boxes}) or [1]
        blocks.append({
            "id": "b-page-header",
            "type": "table",
            "title": "Page header",
            "headers": names,
            "rows": [{"id": "r-page-header", "page": pages[0], "cells": cells}],
            "source_pages": pages,
            "bbox": {str(p): _union([b for b in boxes if b["page"] == p]) for p in pages},
        })

    seen = {name for c in clusters for name in (f.field_name for f in c["facts"])}
    columns = [n for n in field_order if n in seen] + sorted(seen - set(field_order))
    rows = []
    for cluster in clusters:
        by_field: Dict[str, Fact] = {}
        for fact in cluster["facts"]:
            by_field.setdefault(fact.field_name, fact)
        row_group = next((f.row_group_id for f in cluster["facts"] if f.row_group_id), None)
        row_id = f"r-{row_group}" if row_group else _stable_id("r", *sorted(str(f.id) for f in cluster["facts"]))
        cells = [
            _fact_cell(by_field[col], page_number_by_id, pre_edit) if col in by_field else _empty_cell()
            for col in columns
        ]
        flags = []
        if any(len({b["page"] for b in c["regions"]}) > 1 for c in cells):
            flags.append("stitched")
        if any(c.get("status") == "in_review" for c in cells):
            flags.append("needs_review")
        rows.append({"id": row_id, "page": cluster["page_number"], "cells": cells, "flags": flags})

    if rows:
        boxes = [b for row in rows for c in row["cells"] for b in c["regions"]]
        pages = sorted({row["page"] for row in rows} | {b["page"] for b in boxes})
        blocks.append({
            "id": "b-facts-table",
            "type": "table",
            "title": "Extracted table",
            "headers": columns,
            "rows": rows,
            "source_pages": pages,
            "bbox": {str(p): _union([b for b in boxes if b["page"] == p]) for p in pages},
        })

    for order, block in enumerate(blocks):
        block["order"] = order
        confidences = [c["confidence"] for r in block.get("rows", []) for c in r["cells"] if c.get("confidence") is not None]
        block["confidence"] = round(sum(confidences) / len(confidences), 4) if confidences else None
        block["flags"] = sorted({f for r in block.get("rows", []) for f in r.get("flags", [])})
    return blocks


async def plan_document_blocks(db: AsyncSession, doc: Document) -> Tuple[str, List[Dict[str, Any]], Dict[str, int]]:
    """Which builder a document gets, and its blocks (see review_blocks.py).

    Template-extracted Facts win: they are the single source of truth for
    those values. Otherwise the archived OCR result for this exact file
    (best source per page), else the search chunks' text."""
    from app.models.document_version import DocumentVersion
    from app.services import review_blocks

    blocks = await _build_blocks_from_facts(db, doc)
    if blocks:
        return "facts", blocks, {"facts": len({p for b in blocks for p in b.get("source_pages", [])})}

    version = await db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    _, pages = await review_blocks.load_ocr_pages(db, version.file_hash if version else None)
    if not pages:
        pages = await review_blocks.load_chunk_pages(db, doc.tenant_id, doc.id, doc.current_version_id)
    blocks, used = review_blocks.blocks_from_pages(pages)
    return review_blocks.builder_name(used, has_facts=False), blocks, used


def _initial_state_blocks(original_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The working copy: structure only. Fact cells become bare references;
    their value is always read live from doc_dg_facts."""
    out = []
    for block in original_blocks:
        b = {"id": block["id"], "type": block["type"]}
        if block["type"] == "table":
            b["rows"] = [
                {
                    "id": row["id"],
                    "cells": [
                        {"fact_id": c["fact_id"]} if c.get("fact_id") else {"text": c.get("text", "")}
                        for c in row["cells"]
                    ],
                }
                for row in block["rows"]
            ]
        else:
            b["text"] = block.get("text", "")
        out.append(b)
    return out


async def _ensure_review(db: AsyncSession, doc: Document) -> Tuple[ReviewOriginal, ReviewState]:
    state = await db.get(ReviewState, doc.id)
    if state is not None:
        original = await db.get(ReviewOriginal, state.original_id)
        return original, state

    builder, blocks, _ = await plan_document_blocks(db, doc)
    return await create_review(db, doc, builder, blocks)


async def create_review(db: AsyncSession, doc: Document, builder: str, blocks: List[Dict[str, Any]]) -> Tuple[ReviewOriginal, ReviewState]:
    """Snapshot + working copy for a document (shared by first open and the
    backfill script, so both produce exactly the same thing)."""
    # ON CONFLICT: two reviewers opening a never-reviewed document at the
    # same moment must not both fail -- the loser just reads the winner's.
    await db.execute(
        pg_insert(ReviewOriginal)
        .values(
            id=uuid_module.uuid4(), tenant_id=doc.tenant_id, document_id=doc.id,
            version_id=doc.current_version_id, builder=builder, blocks=blocks,
            created_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing(constraint="uq_doc_dg_review_originals_doc_version")
    )
    original = (await db.execute(
        select(ReviewOriginal).where(
            ReviewOriginal.document_id == doc.id,
            ReviewOriginal.version_id == doc.current_version_id
            if doc.current_version_id else ReviewOriginal.version_id.is_(None),
        )
    )).scalars().first()
    await db.execute(
        pg_insert(ReviewState)
        .values(
            document_id=doc.id, tenant_id=doc.tenant_id, original_id=original.id, version=1,
            blocks=_initial_state_blocks(original.blocks), updated_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["document_id"])
    )
    await db.flush()
    state = await db.get(ReviewState, doc.id)
    return original, state


# --------------------------------------------------------------------------
# Reading: the full review document
# --------------------------------------------------------------------------

async def _get_document(db: AsyncSession, tenant_id: UUID, document_id: UUID) -> Document:
    # Runs on the RLS session, so a document outside the caller's
    # department scope is simply not found -- same 404 as a wrong id.
    doc = await db.get(Document, document_id)
    if not doc or doc.tenant_id != tenant_id or doc.is_trashed:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _original_index(original: ReviewOriginal) -> Tuple[Dict[str, Dict], Dict[Tuple[str, str], Dict], Dict[str, Dict]]:
    blocks = {b["id"]: b for b in original.blocks}
    rows = {(b["id"], r["id"]): r for b in original.blocks for r in b.get("rows", [])}
    cells_by_fact = {c["fact_id"]: c for b in original.blocks for r in b.get("rows", []) for c in r["cells"] if c.get("fact_id")}
    return blocks, rows, cells_by_fact


async def _load_facts(db: AsyncSession, tenant_id: UUID, document_id: UUID, state_blocks: List[Dict[str, Any]]) -> Dict[str, Fact]:
    """The facts the review state references, keyed by id. Selected by
    document, not by an IN-list of ids: a 68-page register references ~6,300
    facts, and one bind parameter per id made this query alone take ~3s."""
    ids = {c["fact_id"] for b in state_blocks for r in b.get("rows", []) for c in r["cells"] if c.get("fact_id")}
    if not ids:
        return {}
    res = await db.execute(select(Fact).where(Fact.tenant_id == tenant_id, Fact.document_id == document_id))
    return {str(f.id): f for f in res.scalars().all() if str(f.id) in ids}


class _FactView:
    """Read-only slice of a fact row -- all get_review_document needs.
    Building full ORM objects for a ~6,300-fact register took ~2.4s alone."""
    __slots__ = ("id", "field_name", "value", "status", "confidence", "edit_version")

    def __init__(self, id, field_name, value, status, confidence, edit_version):
        self.id, self.field_name, self.value = id, field_name, value
        self.status, self.confidence, self.edit_version = status, confidence, edit_version


async def _read_facts(db: AsyncSession, tenant_id: UUID, document_id: UUID) -> Dict[str, "_FactView"]:
    res = await db.execute(
        select(Fact.id, Fact.field_name, Fact.value, Fact.status, Fact.confidence, Fact.edit_version)
        .where(Fact.tenant_id == tenant_id, Fact.document_id == document_id)
    )
    return {str(row[0]): _FactView(*row) for row in res.all()}


def _attestation_holds(att: Optional[Dict[str, Any]], facts: Dict[str, Fact]) -> bool:
    if not att:
        return False
    for fact_id, version in (att.get("fact_versions") or {}).items():
        fact = facts.get(fact_id)
        if fact is None or fact.edit_version != version:
            return False
    return True


async def _history_counts(db: AsyncSession, tenant_id: UUID, document_id: UUID) -> Dict[str, int]:
    res = await db.execute(
        select(ReviewAuditEntry.block_id, ReviewAuditEntry.row_id, ReviewAuditEntry.col)
        .where(ReviewAuditEntry.tenant_id == tenant_id, ReviewAuditEntry.document_id == document_id)
    )
    counts: Dict[str, int] = {}
    for block_id, row_id, col in res.all():
        for key in (f"{block_id}", f"{block_id}/{row_id}", f"{block_id}/{row_id}/{col}"):
            counts[key] = counts.get(key, 0) + 1
    return counts


async def get_review_document(db: AsyncSession, tenant_id: UUID, document_id: UUID, role: str) -> Dict[str, Any]:
    doc = await _get_document(db, tenant_id, document_id)
    original, state = await _ensure_review(db, doc)
    orig_blocks, orig_rows, orig_cells = _original_index(original)
    facts = await _read_facts(db, tenant_id, document_id)
    history = await _history_counts(db, tenant_id, document_id)
    low_conf = await get_float("review_low_confidence_threshold", 0.6)

    edit_count = 0
    blocks_out = []
    for order, sb in enumerate(state.blocks):
        ob = orig_blocks.get(sb["id"], {})
        added = bool(sb.get("added"))
        deleted = bool(sb.get("deleted"))
        block: Dict[str, Any] = {
            "id": sb["id"], "type": sb["type"], "order": order,
            "title": ob.get("title"),
            "added": added, "deleted": deleted,
            "source_pages": ob.get("source_pages") or sb.get("source_pages") or [],
            "bbox": ob.get("bbox") or {},
            "confidence": ob.get("confidence"),
            "flags": list(ob.get("flags") or []),
            "history_count": history.get(sb["id"], 0),
        }
        if added or deleted:
            edit_count += 1

        if sb["type"] == "table":
            block["headers"] = ob.get("headers") or sb.get("headers") or []
            rows_out = []
            any_edit = False
            all_verified = True
            for ri, sr in enumerate(sb["rows"]):
                orow = orig_rows.get((sb["id"], sr["id"]), {})
                row_added = bool(sr.get("added"))
                row_deleted = bool(sr.get("deleted"))
                if row_added or row_deleted:
                    edit_count += 1
                    any_edit = True
                cells_out = []
                row_edited = row_added
                row_fact_ids = []
                all_cells_verified = True
                for ci, sc in enumerate(sr["cells"]):
                    key = f"{sb['id']}/{sr['id']}/{ci}"
                    if sc.get("fact_id"):
                        fact = facts.get(sc["fact_id"])
                        oc = orig_cells.get(sc["fact_id"], {})
                        if fact is None:
                            cells_out.append({"fact_id": sc["fact_id"], "text": "", "missing": True, "status": MACHINE,
                                              "edited": False, "original": oc.get("text", "")})
                            continue
                        row_fact_ids.append(sc["fact_id"])
                        text = _as_text(fact.value)
                        edited = fact.value != oc.get("value_json", fact.value)
                        if fact.status != "verified":
                            all_cells_verified = False
                        status = VERIFIED if fact.status == "verified" else (EDITED if edited else MACHINE)
                        cells_out.append({
                            "fact_id": sc["fact_id"], "field_name": fact.field_name,
                            "text": text, "original": oc.get("text", text),
                            "edited": edited, "status": status, "fact_status": fact.status,
                            "fact_version": fact.edit_version,
                            "revertable": edited and sc.get("rv") == fact.edit_version,
                            "changed_elsewhere": edited and sc.get("rv") != fact.edit_version,
                            "confidence": fact.confidence,
                            "low_confidence": fact.confidence is not None and fact.confidence < low_conf,
                            "bbox": oc.get("bbox"), "regions": oc.get("regions") or [],
                            "history_count": history.get(key, 0),
                        })
                    else:
                        ocells = orow.get("cells") or []
                        otext = ocells[ci].get("text", "") if ci < len(ocells) and not row_added else ""
                        text = sc.get("text", "")
                        edited = text != otext
                        all_cells_verified = False
                        ocell = ocells[ci] if ci < len(ocells) and not row_added else {}
                        conf = ocell.get("confidence")
                        cells_out.append({
                            "fact_id": None, "text": text, "original": otext,
                            "edited": edited, "status": EDITED if edited else MACHINE,
                            "revertable": edited and not row_added,
                            "confidence": conf, "low_confidence": conf is not None and conf < low_conf,
                            "bbox": ocell.get("bbox"), "regions": ocell.get("regions") or [],
                            "history_count": history.get(key, 0),
                        })
                    if cells_out[-1]["edited"]:
                        row_edited = True
                        if not row_added and not row_deleted:
                            edit_count += 1
                if row_edited:
                    any_edit = True
                att = sr.get("verification")
                row_verified = bool(_attestation_holds(att, facts) or (row_fact_ids and all_cells_verified)) and not row_deleted
                if not row_verified and not row_deleted:
                    all_verified = False
                rows_out.append({
                    "id": sr["id"], "index": ri,
                    "page": orow.get("page") or sr.get("page"),
                    "added": row_added, "deleted": row_deleted,
                    "flags": orow.get("flags") or [],
                    "status": VERIFIED if row_verified else (EDITED if row_edited or row_deleted else MACHINE),
                    "verified_by": (att or {}).get("by") if row_verified and att else None,
                    "verified_at": (att or {}).get("at") if row_verified and att else None,
                    "cells": cells_out,
                    "history_count": history.get(f"{sb['id']}/{sr['id']}", 0),
                })
            block["rows"] = rows_out
            live_rows = [r for r in rows_out if not r["deleted"]]
            block["status"] = VERIFIED if live_rows and all_verified else (EDITED if any_edit or added else MACHINE)
        else:
            otext = ob.get("text", "") if not added else ""
            text = sb.get("text", "")
            edited = (text != otext) and not added
            if edited:
                edit_count += 1
            att = sb.get("verification")
            block.update({
                "text": text, "original": otext, "edited": edited, "revertable": edited,
                "status": VERIFIED if att and not deleted else (EDITED if edited or added or deleted else MACHINE),
                "verified_by": (att or {}).get("by"), "verified_at": (att or {}).get("at"),
            })
        blocks_out.append(block)

    return {
        "document_id": str(doc.id),
        "title": doc.title,
        "mime_type": doc.mime_type,
        "page_count": doc.pages_total_count or max(
            [p for b in blocks_out for p in b["source_pages"]] or [1]
        ),
        "builder": original.builder,
        "version": state.version,
        "etag": f'"{state.version}"',
        "edit_count": edit_count,
        "is_clean": edit_count == 0,
        "low_confidence_threshold": low_conf,
        "blocks": blocks_out,
        "permissions": permissions_for(role),
    }


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _require(role: Any, key: str, action: str) -> None:
    if not grants(role, key):
        raise HTTPException(status_code=403, detail=f"{action} is not allowed for your role ({key})")


async def _lock_state(db: AsyncSession, tenant_id: UUID, document_id: UUID, expected_version: Optional[int]) -> Tuple[Document, ReviewOriginal, ReviewState]:
    doc = await _get_document(db, tenant_id, document_id)
    original, _ = await _ensure_review(db, doc)
    state = (await db.execute(
        select(ReviewState).where(ReviewState.document_id == document_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalars().one()
    if expected_version is None:
        raise HTTPException(status_code=428, detail="If-Match header with the review version is required")
    if expected_version != state.version:
        raise HTTPException(status_code=409, detail={
            "code": "stale_document",
            "message": "Someone else saved changes to this document since you loaded it. Reload to see the latest.",
            "expected_version": expected_version,
            "current_version": state.version,
        })
    return doc, original, state


def _find_block(state: ReviewState, block_id: str) -> Tuple[int, Dict[str, Any]]:
    for i, b in enumerate(state.blocks):
        if b["id"] == block_id:
            return i, b
    raise HTTPException(status_code=404, detail=f"Block {block_id} not found")


def _find_row(block: Dict[str, Any], row_id: str) -> Tuple[int, Dict[str, Any]]:
    if block["type"] != "table":
        raise HTTPException(status_code=400, detail=f"Block {block['id']} is not a table")
    for i, r in enumerate(block["rows"]):
        if r["id"] == row_id:
            return i, r
    raise HTTPException(status_code=404, detail=f"Row {row_id} not found in block {block['id']}")


def _check_col(row: Dict[str, Any], col: int) -> Dict[str, Any]:
    if col < 0 or col >= len(row["cells"]):
        raise HTTPException(status_code=400, detail=f"Column {col} is out of range (0..{len(row['cells']) - 1})")
    return row["cells"][col]


async def _audit(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, action: str, *,
    block_id: Optional[str] = None, row_id: Optional[str] = None, row: Optional[int] = None,
    col: Optional[int] = None, fact_id: Optional[str] = None, old_value: Any = None, new_value: Any = None,
) -> ReviewAuditEntry:
    entry = ReviewAuditEntry(
        id=uuid_module.uuid4(), tenant_id=tenant_id, document_id=document_id,
        block_id=block_id, row_id=row_id, row=row, col=col,
        fact_id=UUID(fact_id) if fact_id else None, action=action,
        old_value=old_value, new_value=new_value, user_id=actor_id, created_at=datetime.utcnow(),
        entry_hash="",
    )
    entry.entry_hash = hashlib.sha256(json.dumps({
        "id": str(entry.id), "tenant_id": str(tenant_id), "document_id": str(document_id),
        "block_id": block_id, "row_id": row_id, "row": row, "col": col, "fact_id": fact_id,
        "action": action, "old_value": old_value, "new_value": new_value,
        "user_id": str(actor_id), "created_at": entry.created_at.isoformat(),
    }, sort_keys=True, default=str).encode()).hexdigest()
    db.add(entry)
    await db.flush()
    # The hash-chained main trail carries this row's id and hash, so the two
    # trails can be cross-checked entry for entry.
    await log_action(
        db, actor_id, tenant_id, f"review.{action}",
        resource_type="document", resource_id=document_id,
        details={
            "review_audit_id": str(entry.id), "review_audit_hash": entry.entry_hash,
            "block_id": block_id, "row_id": row_id, "col": col, "fact_id": fact_id,
        },
    )
    return entry


async def _finish(db: AsyncSession, tenant_id: UUID, document_id: UUID, role: str) -> Dict[str, Any]:
    """After a saved change: the full document (what the UI shows), with the
    search index brought in line with it in the same transaction."""
    from app.services import review_search

    result = await get_review_document(db, tenant_id, document_id, role)
    doc = await db.get(Document, document_id)
    changed, index_changed = await review_search.sync_review_chunks(db, doc, result)
    if index_changed:
        # Cached search results (5 min, per tenant) would otherwise keep
        # showing the old -- or an undone -- correction.
        from app.services.cache_service import invalidate_tenant_cache

        await invalidate_tenant_cache(str(tenant_id))
    if changed:
        # Queued only once this request's transaction has committed, so the
        # worker never looks for a chunk that isn't visible yet -- and never
        # at all if the save rolls back.
        db.info.setdefault("review_embed_ids", []).extend(changed)
        if not db.info.get("review_embed_hooked"):
            db.info["review_embed_hooked"] = True
            event.listen(db.sync_session, "after_commit", _queue_embeddings_after_commit)
            event.listen(db.sync_session, "after_rollback", _drop_pending_embeddings)
    return result


def _queue_embeddings_after_commit(session) -> None:
    from app.services import review_search

    review_search.enqueue_embedding(session.info.pop("review_embed_ids", []))


def _drop_pending_embeddings(session) -> None:
    session.info.pop("review_embed_ids", None)


def _touch(state: ReviewState, actor_id: UUID) -> None:
    state.version += 1
    state.updated_at = datetime.utcnow()
    state.updated_by = actor_id
    # The blocks were mutated in place, which also mutated SQLAlchemy's own
    # snapshot of the loaded value -- reassigning an equal copy would be
    # detected as "no change" and silently never written.
    flag_modified(state, "blocks")


async def _fact_for_update(db: AsyncSession, tenant_id: UUID, fact_id: str) -> Fact:
    fact = (await db.execute(
        select(Fact).where(Fact.id == UUID(fact_id), Fact.tenant_id == tenant_id).with_for_update()
    )).scalars().first()
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return fact


async def _propose_entity_rematch(db: AsyncSession, tenant_id: UUID, fact: Fact, actor_id: UUID) -> List[str]:
    """A corrected person/property name should point at the right entity --
    but a human edit is not evidence of identity on its own, so the new
    link is proposed as a tier-3 edge, which always lands 'held' and waits
    in the Entities approval queue. Existing machine edges stay as they are
    (machine-made links keep that label, T56)."""
    from app.services import entity_graph_service as eg

    entity_type = eg._classify_fact_field(fact.field_name)
    label = _as_text(fact.value).strip()
    if entity_type is None or not eg._is_meaningful_label(label):
        return []
    similar = await eg.find_similar_nodes(db, tenant_id, entity_type, label)
    if similar:
        node_id = UUID(similar[0]["id"])
    else:
        node = await eg.create_node(
            db, tenant_id, entity_type, label, actor_id=actor_id,
            attributes={"created_from": "review_edit", "fact_id": str(fact.id)},
        )
        node_id = node.id
    edge = await eg.create_edge(
        db, tenant_id, edge_type="mentioned_in", tier=3,
        source_node_id=node_id, target_type="fact", target_fact_id=fact.id,
        confidence=None, evidence_fact_id=fact.id,
        created_by_actor_id=actor_id, created_by_policy_version=REMATCH_POLICY_VERSION,
    )
    return [str(edge.id)]


async def _withdraw_rematch(db: AsyncSession, tenant_id: UUID, edge_ids: List[str], actor_id: UUID) -> None:
    """Undoing the edit withdraws its still-pending proposals. One a human
    already confirmed is left alone -- that is now their decision."""
    from app.services import entity_graph_service as eg

    for edge_id in edge_ids or []:
        edge = await db.get(EntityEdge, UUID(edge_id))
        if edge is not None and edge.tenant_id == tenant_id and edge.status == "held":
            await eg.delete_edge(db, tenant_id, edge.id, actor_id)


async def _set_fact_value(
    db: AsyncSession, tenant_id: UUID, fact: Fact, new_text: str, expected_version: Optional[int], actor_id: UUID,
    document_id: UUID,
) -> Any:
    new_value = dict(fact.value) if isinstance(fact.value, dict) else {}
    new_value["v"] = new_text
    await fact_verification_service.bulk_edit_facts(
        db, tenant_id,
        [{"fact_id": fact.id, "new_value": new_value, "expected_version": expected_version}],
        actor_id, audit_details={"source": "review_screen", "document_id": str(document_id)},
    )
    return new_value


async def _restore_fact(db: AsyncSession, tenant_id: UUID, fact: Fact, original_cell: Dict[str, Any], actor_id: UUID) -> None:
    """Back to the OCR reading: original value AND original review status
    (an edit had forced 'in_review'; the untouched machine value was
    'machine'). Not bulk_edit_facts -- that path always lands in_review by
    design, which is right for a correction but wrong for an undo."""
    previous = {"value": fact.value, "status": fact.status}
    fact.value = original_cell["value_json"]
    # A fact that was already 'verified' before the edit lost that
    # attestation when it was edited (bulk_edit_facts clears it); restoring
    # the word 'verified' without the who/when would be a fake
    # confirmation, so it comes back as in_review and needs a fresh one.
    original_status = original_cell.get("status") or "machine"
    fact.status = "in_review" if original_status == "verified" else original_status
    fact.verified_by_actor_id = None
    fact.verified_at = None
    fact.claimed_by_actor_id = None
    fact.claimed_at = None
    bump_edit_version(fact)
    await db.flush()
    await log_action(
        db, actor_id, tenant_id, "fact.review_revert",
        resource_type="fact", resource_id=fact.id,
        details={"previous_value": previous["value"], "previous_status": previous["status"],
                 "restored_value": fact.value, "restored_status": fact.status},
    )


async def edit_cell(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, row_id: str, col: int, value: str, expected_fact_version: Optional[int] = None,
) -> Dict[str, Any]:
    _require(role, EDIT_PERMISSION, "Editing")
    _, original, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, block = _find_block(state, block_id)
    ri, row = _find_row(block, row_id)
    cell = _check_col(row, col)
    if row.get("deleted"):
        raise HTTPException(status_code=400, detail="Restore the deleted row before editing it")

    if cell.get("fact_id"):
        fact = await _fact_for_update(db, tenant_id, cell["fact_id"])
        check_edit_version(fact, expected_fact_version)
        old_text = _as_text(fact.value)
        if old_text == value:
            return await get_review_document(db, tenant_id, document_id, role)
        await _set_fact_value(db, tenant_id, fact, value, expected_fact_version, actor_id, document_id)
        cell["rv"] = fact.edit_version
        cell["rematch_edge_ids"] = (cell.get("rematch_edge_ids") or []) + await _propose_entity_rematch(db, tenant_id, fact, actor_id)
        await _audit(db, tenant_id, document_id, actor_id, "edit_cell", block_id=block_id, row_id=row_id,
                     row=ri, col=col, fact_id=cell["fact_id"], old_value=old_text, new_value=value)
    else:
        old_text = cell.get("text", "")
        if old_text == value:
            return await get_review_document(db, tenant_id, document_id, role)
        cell["text"] = value
        row.pop("verification", None)  # the attestation covered the old text
        await _audit(db, tenant_id, document_id, actor_id, "edit_cell", block_id=block_id, row_id=row_id,
                     row=ri, col=col, old_value=old_text, new_value=value)
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def edit_text_block(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, text: str,
) -> Dict[str, Any]:
    _require(role, EDIT_PERMISSION, "Editing")
    _, _, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, block = _find_block(state, block_id)
    if block["type"] not in TEXT_BLOCK_TYPES:
        raise HTTPException(status_code=400, detail=f"Block {block_id} is a {block['type']}, not a text block")
    if block.get("deleted"):
        raise HTTPException(status_code=400, detail="Restore the deleted block before editing it")
    old = block.get("text", "")
    if old == text:
        return await get_review_document(db, tenant_id, document_id, role)
    block["text"] = text
    block.pop("verification", None)  # attested text changed -- attestation lapses
    await _audit(db, tenant_id, document_id, actor_id, "edit_text", block_id=block_id, old_value=old, new_value=text)
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def revert(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, row_id: Optional[str] = None, col: Optional[int] = None,
) -> Dict[str, Any]:
    """Undo one change: a cell (row_id + col), a row deletion (row_id), or a
    block's text/deletion (block_id alone)."""
    _require(role, EDIT_PERMISSION, "Reverting")
    _, original, state = await _lock_state(db, tenant_id, document_id, expected_version)
    orig_blocks, orig_rows, orig_cells = _original_index(original)
    _, block = _find_block(state, block_id)

    if row_id is not None and col is not None:
        ri, row = _find_row(block, row_id)
        cell = _check_col(row, col)
        if cell.get("fact_id"):
            fact = await _fact_for_update(db, tenant_id, cell["fact_id"])
            oc = orig_cells.get(cell["fact_id"])
            if oc is None or fact.value == oc["value_json"]:
                raise HTTPException(status_code=400, detail="This cell has no correction to revert")
            if cell.get("rv") != fact.edit_version:
                raise HTTPException(status_code=409, detail={
                    "code": "changed_elsewhere",
                    "message": "This value was changed outside the review screen after your correction, so it can't be reverted here. Reload to see the latest.",
                    "fact_id": cell["fact_id"],
                })
            old_text = _as_text(fact.value)
            await _restore_fact(db, tenant_id, fact, oc, actor_id)
            await _withdraw_rematch(db, tenant_id, cell.pop("rematch_edge_ids", []), actor_id)
            cell.pop("rv", None)
            await _audit(db, tenant_id, document_id, actor_id, "revert_cell", block_id=block_id, row_id=row_id,
                         row=ri, col=col, fact_id=cell["fact_id"], old_value=old_text, new_value=oc["text"])
        else:
            if row.get("added"):
                raise HTTPException(status_code=400, detail="Cells of an added row have no original value; delete the row instead")
            orow = orig_rows.get((block_id, row_id), {})
            otext = (orow.get("cells") or [{}] * (col + 1))[col].get("text", "")
            if cell.get("text", "") == otext:
                raise HTTPException(status_code=400, detail="This cell has no correction to revert")
            old_text = cell.get("text", "")
            cell["text"] = otext
            row.pop("verification", None)
            await _audit(db, tenant_id, document_id, actor_id, "revert_cell", block_id=block_id, row_id=row_id,
                         row=ri, col=col, old_value=old_text, new_value=otext)
    elif row_id is not None:
        ri, row = _find_row(block, row_id)
        if not row.get("deleted"):
            raise HTTPException(status_code=400, detail="Only a deleted row can be reverted as a whole; revert its cells individually")
        row.pop("deleted", None)
        await _audit(db, tenant_id, document_id, actor_id, "revert_delete_row", block_id=block_id, row_id=row_id, row=ri)
    else:
        if block.get("deleted"):
            block.pop("deleted", None)
            await _audit(db, tenant_id, document_id, actor_id, "revert_delete_block", block_id=block_id)
        elif block["type"] in TEXT_BLOCK_TYPES and not block.get("added"):
            otext = orig_blocks.get(block_id, {}).get("text", "")
            if block.get("text", "") == otext:
                raise HTTPException(status_code=400, detail="This block has no correction to revert")
            old = block.get("text", "")
            block["text"] = otext
            block.pop("verification", None)
            await _audit(db, tenant_id, document_id, actor_id, "revert_text", block_id=block_id, old_value=old, new_value=otext)
        else:
            raise HTTPException(status_code=400, detail="Nothing to revert on this block (revert table cells individually; delete added blocks)")
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def add_row(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, after_row_id: Optional[str],
) -> Dict[str, Any]:
    _require(role, EDIT_PERMISSION, "Adding rows")
    _, original, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, block = _find_block(state, block_id)
    if block["type"] != "table":
        raise HTTPException(status_code=400, detail=f"Block {block_id} is not a table")
    width = len(_original_index(original)[0].get(block_id, {}).get("headers") or []) or (
        len(block["rows"][0]["cells"]) if block["rows"] else 1
    )
    if after_row_id is None:
        index = len(block["rows"])
        page = None
    else:
        ri, after = _find_row(block, after_row_id)
        index = ri + 1
        page = after.get("page") or _original_index(original)[1].get((block_id, after_row_id), {}).get("page")
    new_row = {"id": f"r-added-{uuid_module.uuid4().hex[:12]}", "added": True, "page": page,
               "cells": [{"text": ""} for _ in range(width)]}
    block["rows"].insert(index, new_row)
    await _audit(db, tenant_id, document_id, actor_id, "add_row", block_id=block_id, row_id=new_row["id"], row=index,
                 new_value={"after_row_id": after_row_id})
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def delete_row(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, row_id: str,
) -> Dict[str, Any]:
    """An added row is removed outright (the audit keeps it). An extracted
    row is only hidden -- its facts are untouched and it can be restored."""
    _require(role, EDIT_PERMISSION, "Deleting rows")
    _, _, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, block = _find_block(state, block_id)
    ri, row = _find_row(block, row_id)
    if row.get("deleted"):
        raise HTTPException(status_code=400, detail="Row is already deleted")
    old = [c.get("text") if not c.get("fact_id") else {"fact_id": c["fact_id"]} for c in row["cells"]]
    if row.get("added"):
        block["rows"].pop(ri)
    else:
        row["deleted"] = True
        row.pop("verification", None)
    await _audit(db, tenant_id, document_id, actor_id, "delete_row", block_id=block_id, row_id=row_id, row=ri, old_value=old)
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def add_block(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_type: str, text: str, after_block_id: Optional[str], page: Optional[int],
) -> Dict[str, Any]:
    _require(role, EDIT_PERMISSION, "Adding blocks")
    if block_type not in TEXT_BLOCK_TYPES:
        raise HTTPException(status_code=400, detail=f"Only {' / '.join(TEXT_BLOCK_TYPES)} blocks can be added")
    _, _, state = await _lock_state(db, tenant_id, document_id, expected_version)
    index = len(state.blocks) if after_block_id is None else _find_block(state, after_block_id)[0] + 1
    new_block = {"id": f"b-added-{uuid_module.uuid4().hex[:12]}", "type": block_type, "added": True, "text": text,
                 "source_pages": [page] if page else []}
    state.blocks.insert(index, new_block)
    await _audit(db, tenant_id, document_id, actor_id, "add_block", block_id=new_block["id"],
                 new_value={"type": block_type, "text": text, "after_block_id": after_block_id})
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def delete_block(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str,
) -> Dict[str, Any]:
    _require(role, EDIT_PERMISSION, "Deleting blocks")
    _, _, state = await _lock_state(db, tenant_id, document_id, expected_version)
    bi, block = _find_block(state, block_id)
    if block.get("deleted"):
        raise HTTPException(status_code=400, detail="Block is already deleted")
    if block.get("added"):
        state.blocks.pop(bi)
    else:
        block["deleted"] = True
        block.pop("verification", None)
    await _audit(db, tenant_id, document_id, actor_id, "delete_block", block_id=block_id,
                 old_value={"type": block["type"], "text": block.get("text")})
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def revert_all(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
) -> Dict[str, Any]:
    """Back to raw extraction. Facts changed outside the review screen since
    this screen's last write are left alone and reported in `skipped` --
    the same rule as a single revert, applied cell by cell."""
    _require(role, REVERT_ALL_PERMISSION, "Revert all")
    _, original, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, _, orig_cells = _original_index(original)
    facts = await _load_facts(db, tenant_id, document_id, state.blocks)

    reverted, skipped = [], []
    for block in state.blocks:
        for row in block.get("rows", []):
            for cell in row["cells"]:
                fid = cell.get("fact_id")
                if not fid or fid not in facts:
                    continue
                oc = orig_cells.get(fid)
                fact = facts[fid]
                if oc is None or fact.value == oc["value_json"]:
                    await _withdraw_rematch(db, tenant_id, cell.get("rematch_edge_ids", []), actor_id)
                    continue
                if cell.get("rv") != fact.edit_version:
                    skipped.append({"fact_id": fid, "reason": "changed_elsewhere"})
                    continue
                await _restore_fact(db, tenant_id, fact, oc, actor_id)
                await _withdraw_rematch(db, tenant_id, cell.get("rematch_edge_ids", []), actor_id)
                reverted.append(fid)

    # Unconfirm facts this screen confirmed (row attestations), when unchanged since.
    for block in state.blocks:
        for row in block.get("rows", []):
            for fid in ((row.get("verification") or {}).get("confirmed_fact_ids") or []):
                fact = facts.get(fid)
                if fact is not None and fact.status == "verified" and \
                        fact.edit_version == (row["verification"].get("fact_versions") or {}).get(fid):
                    await fact_verification_service.unconfirm_fact(db, tenant_id, fact.id, actor_id)

    fresh = _initial_state_blocks(original.blocks)
    # A fact that could not be reverted keeps its review bookkeeping.
    skipped_ids = {s["fact_id"] for s in skipped}
    if skipped_ids:
        old_cells = {c["fact_id"]: c for b in state.blocks for r in b.get("rows", []) for c in r["cells"] if c.get("fact_id")}
        for b in fresh:
            for r in b.get("rows", []):
                for c in r["cells"]:
                    if c.get("fact_id") in skipped_ids:
                        c.update({k: v for k, v in old_cells[c["fact_id"]].items() if k in ("rv",)})
    state.blocks = fresh
    await _audit(db, tenant_id, document_id, actor_id, "revert_all",
                 new_value={"reverted_fact_ids": reverted, "skipped": skipped})
    _touch(state, actor_id)
    await db.flush()
    result = await _finish(db, tenant_id, document_id, role)
    result["revert_all"] = {"reverted": len(reverted), "skipped": skipped}
    return result


async def set_verified(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, actor_id: UUID, role: str, expected_version: Optional[int],
    block_id: str, row_id: Optional[str], verified: bool, fact_versions: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    _require(role, VERIFY_PERMISSION, "Verifying")
    _, _, state = await _lock_state(db, tenant_id, document_id, expected_version)
    _, block = _find_block(state, block_id)
    now = datetime.utcnow().isoformat()

    if row_id is None:
        if block["type"] == "table":
            raise HTTPException(status_code=400, detail="Verify a table row by row (pass row_id)")
        if block.get("deleted"):
            raise HTTPException(status_code=400, detail="A deleted block cannot be verified")
        old = block.get("verification")
        if verified:
            block["verification"] = {"by": str(actor_id), "at": now}
        else:
            block.pop("verification", None)
        await _audit(db, tenant_id, document_id, actor_id, "verify" if verified else "unverify",
                     block_id=block_id, old_value=old, new_value=block.get("verification"))
    else:
        ri, row = _find_row(block, row_id)
        if row.get("deleted"):
            raise HTTPException(status_code=400, detail="A deleted row cannot be verified")
        old = row.get("verification")
        if verified:
            confirmed = []
            versions: Dict[str, int] = {}
            for cell in row["cells"]:
                fid = cell.get("fact_id")
                if not fid:
                    continue
                fact = await _fact_for_update(db, tenant_id, fid)
                check_edit_version(fact, (fact_versions or {}).get(fid))
                if fact.status == "in_review":
                    await fact_verification_service.confirm_fact(db, tenant_id, fact.id, actor_id)
                    confirmed.append(fid)
                    if cell.get("rv") is not None:
                        cell["rv"] = fact.edit_version  # confirming our own edit keeps it revertable
                versions[fid] = fact.edit_version
            row["verification"] = {"by": str(actor_id), "at": now, "fact_versions": versions,
                                   "confirmed_fact_ids": confirmed}
        else:
            if not old:
                raise HTTPException(status_code=400, detail="This row has no review attestation to remove")
            for fid in old.get("confirmed_fact_ids") or []:
                fact = await _fact_for_update(db, tenant_id, fid)
                if fact.status == "verified" and fact.edit_version == (old.get("fact_versions") or {}).get(fid):
                    await fact_verification_service.unconfirm_fact(db, tenant_id, fact.id, actor_id)
                    cell = next((c for c in row["cells"] if c.get("fact_id") == fid), None)
                    if cell is not None and cell.get("rv") is not None:
                        cell["rv"] = fact.edit_version
            row.pop("verification", None)
        await _audit(db, tenant_id, document_id, actor_id, "verify_row" if verified else "unverify_row",
                     block_id=block_id, row_id=row_id, row=ri, old_value=old, new_value=row.get("verification"))
    _touch(state, actor_id)
    await db.flush()
    return await _finish(db, tenant_id, document_id, role)


async def get_history(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, block_id: str,
    row_id: Optional[str] = None, col: Optional[int] = None,
) -> List[Dict[str, Any]]:
    await _get_document(db, tenant_id, document_id)
    target = ReviewAuditEntry.block_id == block_id
    if row_id is not None:
        target = and_(target, ReviewAuditEntry.row_id == row_id)
    if col is not None:
        target = and_(target, ReviewAuditEntry.col == col)
    # Document-wide actions (revert-all) touched every cell, so they belong
    # in every cell's history too.
    stmt = select(ReviewAuditEntry).where(
        ReviewAuditEntry.tenant_id == tenant_id,
        ReviewAuditEntry.document_id == document_id,
        or_(target, ReviewAuditEntry.block_id.is_(None)),
    )
    entries = list((await db.execute(stmt.order_by(ReviewAuditEntry.created_at.desc()))).scalars().all())

    from app.models.user import User
    user_ids = {e.user_id for e in entries}
    names: Dict[UUID, str] = {}
    if user_ids:
        res = await db.execute(select(User).where(User.id.in_(user_ids)))
        for u in res.scalars().all():
            names[u.id] = getattr(u, "full_name", None) or u.email
    return [
        {
            "id": str(e.id), "action": e.action, "block_id": e.block_id, "row_id": e.row_id,
            "row": e.row, "col": e.col, "fact_id": str(e.fact_id) if e.fact_id else None,
            "old_value": e.old_value, "new_value": e.new_value,
            "user_id": str(e.user_id), "user_name": names.get(e.user_id),
            "created_at": e.created_at.isoformat(), "entry_hash": e.entry_hash,
        }
        for e in entries
    ]


# --------------------------------------------------------------------------
# Page images
# --------------------------------------------------------------------------

PAGE_IMAGE_DPI = 150
_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "bmp", "webp", "tiff", "tif")


async def get_page_image(db: AsyncSession, tenant_id: UUID, document_id: UUID, page_number: int) -> bytes:
    """PNG of one page, rendered server-side so every client draws boxes on
    exactly the same pixels. The document is loaded on the caller's RLS
    session first, so tenant and department scope apply exactly as for any
    other document read. Rendered pages are cached next to the file in
    object storage (keyed by version, so a new upload never serves a stale
    page)."""
    import asyncio
    import os

    from app.models.document_version import DocumentVersion
    from app.pipeline.vlm_extraction import _render_image_page_png, _render_pdf_page_png
    from app.services.storage_service import download_file, upload_file

    doc = await _get_document(db, tenant_id, document_id)
    if page_number < 1:
        raise HTTPException(status_code=400, detail="Page numbers start at 1")
    version = await db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    if version is None:
        raise HTTPException(status_code=404, detail="Document has no stored file")

    ext = os.path.splitext(version.original_filename or version.s3_path)[1].lower().lstrip(".")
    if ext != "pdf" and ext not in _IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail=f"No page image for .{ext} files")
    if ext in _IMAGE_EXTENSIONS and page_number != 1:
        raise HTTPException(status_code=404, detail=f"Page {page_number} not found")

    cache_key = f"{os.path.dirname(version.s3_path)}/review_pages/page_{page_number}_{PAGE_IMAGE_DPI}.png"
    try:
        return await download_file(cache_key)
    except Exception:
        pass

    file_bytes = await download_file(version.s3_path)
    if ext == "pdf":
        rendered = await asyncio.to_thread(_render_pdf_page_png, file_bytes, page_number, PAGE_IMAGE_DPI)
        if rendered is None:
            raise HTTPException(status_code=404, detail=f"Page {page_number} not found")
    else:
        rendered = await asyncio.to_thread(_render_image_page_png, file_bytes)
    png = rendered[0]
    try:
        await upload_file(png, cache_key, "image/png")
    except Exception:
        pass  # a failed cache write must never fail the page itself
    return png


# --------------------------------------------------------------------------
# Workbench "Documents" tab
# --------------------------------------------------------------------------

async def list_review_documents(
    db: AsyncSession, tenant_id: UUID, q: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> Dict[str, Any]:
    """Scanned documents (PDFs and images) that can be checked against their
    scan, with progress per document; those with values still waiting for a
    person come first.

    Runs on the caller's RLS session, so department scope applies exactly as
    for the document list. Value counts are over real (non "_"-prefixed)
    facts; a document without template-extracted values shows zero counts
    and is checked block by block instead."""
    from sqlalchemy import case, func

    from app.models.document_version import DocumentVersion
    from app.models.user import User

    fact_counts = (
        select(
            Fact.document_id.label("document_id"),
            func.count().label("fact_count"),
            func.count().filter(Fact.status == "in_review").label("in_review"),
            func.count().filter(Fact.status == "verified").label("verified"),
        )
        .where(Fact.tenant_id == tenant_id, ~Fact.field_name.startswith("_", autoescape=True))
        .group_by(Fact.document_id)
        .subquery()
    )
    in_review = func.coalesce(fact_counts.c.in_review, 0)
    stmt = (
        select(
            Document.id, Document.title, Document.pages_total_count,
            func.coalesce(fact_counts.c.fact_count, 0), in_review, func.coalesce(fact_counts.c.verified, 0),
            ReviewState.version, ReviewState.updated_at, User.full_name, User.email,
        )
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .outerjoin(fact_counts, fact_counts.c.document_id == Document.id)
        .outerjoin(ReviewState, ReviewState.document_id == Document.id)
        .outerjoin(User, User.id == ReviewState.updated_by)
        .where(
            Document.tenant_id == tenant_id,
            Document.is_trashed.is_(False),
            # only files that have a scan to compare against
            func.lower(DocumentVersion.original_filename).op("~")(r"\.(pdf|png|jpe?g|tiff?|bmp|webp)$"),
        )
    )
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(case((in_review > 0, 0), else_=1), in_review.desc(), Document.title) \
        .limit(min(max(limit, 1), 200)).offset(max(offset, 0))

    items = []
    for row in (await db.execute(stmt)).all():
        (doc_id, title, pages, fact_count, n_review, verified, version, updated_at, name, email) = row
        started = bool(version and version > 1)
        items.append({
            "document_id": str(doc_id),
            "title": title,
            "page_count": pages or 0,
            "fact_count": fact_count,
            "in_review_count": n_review,
            "verified_count": verified,
            "verified_pct": round(100 * verified / fact_count) if fact_count else 0,
            "review_started": started,
            "last_reviewed_at": updated_at.isoformat() if started and updated_at else None,
            "last_reviewed_by": (name or email) if started else None,
        })
    return {"total": total, "items": items}
