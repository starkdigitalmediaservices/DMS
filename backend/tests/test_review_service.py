"""Review screen service (app/services/review_service.py).

Real Postgres, same pattern as test_fact_verification.py: build a tenant, a
document with a 2-row fact table on one page (plus one row stitched onto a
second page), call the service, roll back.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.entity_edge import EntityEdge
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.page import DocumentPage
from app.models.review import ReviewAuditEntry
from app.models.tenant import Tenant
from app.models.user import User
from app.services import fact_verification_service
from app.services import review_service as rs

TABLE = "b-facts-table"


async def _setup(db, *, statuses=("machine", "machine", "in_review", "machine")):
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"Review {uuid.uuid4().hex[:6]}"))
    await db.flush()
    db.add(User(id=actor_id, tenant_id=tenant_id, email=f"rev_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw"))
    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="register.pdf", status="indexed", pages_total_count=2)
    version = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1, s3_path="x/register.pdf",
        file_hash="h", file_size_bytes=1, original_filename="register.pdf",
    )
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version.id
    p1 = DocumentPage(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id, page_number=1, width=600, height=800)
    p2 = DocumentPage(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id, page_number=2, width=600, height=800)
    db.add_all([p1, p2])
    await db.flush()

    row_a, row_b = uuid.uuid4(), uuid.uuid4()
    specs = [
        (row_a, "owner_name", "Abdul Rahim", 0.95, [(p1, 0.10, 0.20)]),
        (row_a, "survey_number", "12/3", 0.40, [(p1, 0.50, 0.20)]),
        (row_b, "owner_name", "Fatima Begum", 0.90, [(p1, 0.10, 0.30)]),
        # stitched: starts at the bottom of page 1, continues on page 2
        (row_b, "survey_number", "45", 0.85, [(p1, 0.50, 0.30), (p2, 0.50, 0.05)]),
    ]
    facts = []
    for (row, name, value, conf, regions), status in zip(specs, statuses):
        fact = Fact(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
                    field_name=name, value={"v": value}, confidence=conf, status=status, row_group_id=row)
        db.add(fact)
        await db.flush()
        for page, x, y in regions:
            db.add(FactRegion(id=uuid.uuid4(), tenant_id=tenant_id, fact_id=fact.id, page_id=page.id,
                              x0=x, y0=y, x1=x + 0.3, y1=y + 0.05))
        facts.append(fact)
    await db.flush()
    return tenant_id, actor_id, doc.id, facts


def _table(doc):
    return next(b for b in doc["blocks"] if b["id"] == TABLE)


def _cell(doc, row, col):
    return _table(doc)["rows"][row]["cells"][col]


async def _open(db, tenant_id, doc_id, role="it_admin"):
    return await rs.get_review_document(db, tenant_id, doc_id, role)


@pytest.mark.asyncio
async def test_opening_builds_blocks_from_facts_with_bboxes_and_stitched_pages():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, _, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            table = _table(doc)
            assert table["headers"] == ["owner_name", "survey_number"]
            assert [c["text"] for c in table["rows"][0]["cells"]] == ["Abdul Rahim", "12/3"]
            assert table["source_pages"] == [1, 2]
            assert set(table["bbox"]) == {"1", "2"}  # outline drawn on every page it covers
            stitched = table["rows"][1]["cells"][1]
            assert [r["page"] for r in stitched["regions"]] == [1, 2]
            assert "stitched" in table["rows"][1]["flags"]
            assert _cell(doc, 0, 1)["low_confidence"] is True  # 0.40 < 0.6
            assert doc["is_clean"] and doc["edit_count"] == 0 and doc["version"] == 1
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_edit_cell_goes_through_fact_edit_path_and_audits_both_trails():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][0]
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"],
                                     TABLE, row["id"], 1, "12/8", expected_fact_version=row["cells"][1]["fact_version"])

            cell = _cell(doc, 0, 1)
            assert cell["text"] == "12/8" and cell["original"] == "12/3"
            assert cell["edited"] and cell["status"] == rs.EDITED and cell["revertable"]
            assert doc["version"] == 2 and doc["edit_count"] == 1 and not doc["is_clean"]

            fact = await db.get(Fact, facts[1].id)
            assert fact.value == {"v": "12/8"}
            assert fact.status == "in_review"  # an edit never verifies

            entry = (await db.execute(select(ReviewAuditEntry).where(ReviewAuditEntry.document_id == doc_id))).scalars().one()
            assert (entry.action, entry.row, entry.col, entry.old_value, entry.new_value) == ("edit_cell", 0, 1, "12/3", "12/8")
            assert entry.user_id == actor_id and entry.fact_id == facts[1].id
            main = (await db.execute(select(AuditLog).where(AuditLog.action == "review.edit_cell",
                                                            AuditLog.resource_id == doc_id))).scalars().one()
            assert main.details["review_audit_id"] == str(entry.id)
            assert main.details["review_audit_hash"] == entry.entry_hash
            # the fact's own trail is the same one the Workbench writes
            assert (await db.execute(select(AuditLog).where(AuditLog.action == "fact.bulk_edit",
                                                            AuditLog.resource_id == facts[1].id))).scalars().one()
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_revert_single_cell_restores_original_value_and_status_and_is_clean_again():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][0]
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 0, "Abdul Rahman")
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 1, "12/8")
            assert doc["edit_count"] == 2

            doc = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 1)
            assert _cell(doc, 0, 1)["text"] == "12/3" and not _cell(doc, 0, 1)["edited"]
            assert _cell(doc, 0, 0)["text"] == "Abdul Rahman"  # the other correction is untouched
            assert doc["edit_count"] == 1

            doc = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 0)
            assert doc["is_clean"] and doc["edit_count"] == 0
            fact = await db.get(Fact, facts[0].id)
            assert fact.value == {"v": "Abdul Rahim"} and fact.status == "machine"
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_revert_is_refused_when_fact_changed_elsewhere_since():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][0]
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 1, "12/8")
            # a Workbench correction lands afterwards
            await fact_verification_service.bulk_edit_facts(db, tenant_id, [{"fact_id": facts[1].id, "new_value": {"v": "12/9"}}], actor_id)

            with pytest.raises(HTTPException) as exc:
                await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 1)
            assert exc.value.status_code == 409 and exc.value.detail["code"] == "changed_elsewhere"
            assert (await db.get(Fact, facts[1].id)).value == {"v": "12/9"}
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_stale_document_version_is_409():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, _ = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row_id = _table(doc)["rows"][0]["id"]
            await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, 0, "A")
            with pytest.raises(HTTPException) as exc:
                await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, 0, "B")
            assert exc.value.status_code == 409 and exc.value.detail["code"] == "stale_document"
            with pytest.raises(HTTPException) as exc:
                await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", None, TABLE, row_id, 0, "B")
            assert exc.value.status_code == 428
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_stale_fact_version_is_409_in_review_and_in_workbench():
    """The Workbench and the review screen share one version per fact."""
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][0]
            loaded = row["cells"][1]["fact_version"]
            # Workbench edits first, sending the version it loaded (same one)
            await fact_verification_service.bulk_edit_facts(
                db, tenant_id, [{"fact_id": facts[1].id, "new_value": {"v": "99"}, "expected_version": loaded}], actor_id)
            # the review screen's write against the old version is refused
            with pytest.raises(HTTPException) as exc:
                await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row["id"], 1,
                                   "12/8", expected_fact_version=loaded)
            assert exc.value.status_code == 409 and exc.value.detail["code"] == "stale_fact"
            # ...and so is a second Workbench write against the old version
            with pytest.raises(HTTPException) as exc:
                await fact_verification_service.bulk_edit_facts(
                    db, tenant_id, [{"fact_id": facts[1].id, "new_value": {"v": "98"}, "expected_version": loaded}], actor_id)
            assert exc.value.status_code == 409
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_add_and_delete_row_including_restoring_a_deleted_extracted_row():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            first = _table(doc)["rows"][0]["id"]

            doc = await rs.add_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, first)
            rows = _table(doc)["rows"]
            assert len(rows) == 3 and rows[1]["added"] and rows[1]["page"] == 1
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, rows[1]["id"], 0, "Missed owner")
            assert _cell(doc, 1, 0)["text"] == "Missed owner"
            doc = await rs.add_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, None)
            assert _table(doc)["rows"][-1]["added"]

            added_id = _table(doc)["rows"][1]["id"]
            doc = await rs.delete_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, added_id)
            doc = await rs.delete_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, _table(doc)["rows"][-1]["id"])
            assert len(_table(doc)["rows"]) == 2 and doc["is_clean"]

            doc = await rs.delete_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, first)
            assert _table(doc)["rows"][0]["deleted"] and doc["edit_count"] == 1
            assert (await db.get(Fact, facts[0].id)) is not None  # facts are never deleted
            doc = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, first)
            assert not _table(doc)["rows"][0]["deleted"] and doc["is_clean"]

            with pytest.raises(HTTPException) as exc:
                await rs.delete_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, "r-nope")
            assert exc.value.status_code == 404
            with pytest.raises(HTTPException) as exc:
                await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, first, 7, "x")
            assert exc.value.status_code == 400
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_add_edit_revert_and_delete_text_blocks():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, _ = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            doc = await rs.add_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], "heading", "Schedule A", None, 1)
            added = doc["blocks"][-1]
            assert added["type"] == "heading" and added["added"] and added["status"] == rs.EDITED
            assert [b["id"] for b in doc["blocks"]][0] == TABLE  # no anchor = at the end

            doc = await rs.edit_text_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], added["id"], "Schedule B")
            assert doc["blocks"][-1]["text"] == "Schedule B"
            with pytest.raises(HTTPException) as exc:
                await rs.edit_text_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, "x")
            assert exc.value.status_code == 400
            with pytest.raises(HTTPException) as exc:
                await rs.add_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], "table", "", None, None)
            assert exc.value.status_code == 400

            doc = await rs.add_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], "paragraph", "Note", TABLE, None)
            assert [b["type"] for b in doc["blocks"]] == ["table", "paragraph", "heading"]  # inserted below the table
            assert doc["blocks"][1]["text"] == "Note"
            for block_id in [doc["blocks"][1]["id"], doc["blocks"][2]["id"]]:
                doc = await rs.delete_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], block_id)
            assert [b["id"] for b in doc["blocks"]] == [TABLE] and doc["is_clean"]

            doc = await rs.delete_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE)
            assert doc["blocks"][0]["deleted"] and not doc["is_clean"]
            doc = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE)
            assert not doc["blocks"][0]["deleted"] and doc["is_clean"]
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_revert_all_discards_every_correction_and_withdraws_entity_proposals():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            rows = _table(doc)["rows"]
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, rows[0]["id"], 0, "Abdul Rahman Khan")
            proposals = (await db.execute(select(EntityEdge).where(EntityEdge.target_fact_id == facts[0].id))).scalars().all()
            assert [(e.tier, e.status) for e in proposals] == [(3, "held")]  # waits in the approval queue

            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, rows[1]["id"], 1, "46")
            doc = await rs.add_row(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, None)
            doc = await rs.add_block(db, tenant_id, doc_id, actor_id, "operator", doc["version"], "paragraph", "x", None, None)
            assert doc["edit_count"] == 4

            with pytest.raises(HTTPException) as exc:
                await rs.revert_all(db, tenant_id, doc_id, actor_id, "records_officer", doc["version"])
            assert exc.value.status_code == 403

            doc = await rs.revert_all(db, tenant_id, doc_id, actor_id, "it_admin", doc["version"])
            assert doc["is_clean"] and doc["revert_all"] == {"reverted": 2, "skipped": []}
            assert [b["id"] for b in doc["blocks"]] == [TABLE] and len(_table(doc)["rows"]) == 2
            assert (await db.get(Fact, facts[0].id)).value == {"v": "Abdul Rahim"}
            assert (await db.execute(select(EntityEdge).where(EntityEdge.target_fact_id == facts[0].id))).scalars().all() == []
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_permissions_viewer_read_only_operator_cannot_verify():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, _ = await _setup(db)
            doc = await _open(db, tenant_id, doc_id, role="auditor")
            assert doc["permissions"] == {"can_edit": False, "can_verify": False, "can_revert_all": False}
            row_id = _table(doc)["rows"][0]["id"]
            for role in ("auditor", "legal_counsel", "department_head"):
                with pytest.raises(HTTPException) as exc:
                    await rs.edit_cell(db, tenant_id, doc_id, actor_id, role, doc["version"], TABLE, row_id, 0, "x")
                assert exc.value.status_code == 403
            with pytest.raises(HTTPException) as exc:
                await rs.set_verified(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, True)
            assert exc.value.status_code == 403
            assert (await _open(db, tenant_id, doc_id, role="operator"))["permissions"]["can_verify"] is False
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_verify_row_confirms_in_review_facts_and_lapses_when_a_fact_changes():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][1]  # Fatima Begum (in_review) + 45 (machine)
            doc = await rs.set_verified(db, tenant_id, doc_id, actor_id, "records_officer", doc["version"], TABLE, row["id"], True,
                                        {c["fact_id"]: c["fact_version"] for c in row["cells"]})
            vrow = _table(doc)["rows"][1]
            assert vrow["status"] == rs.VERIFIED and vrow["verified_by"] == str(actor_id)
            assert (await db.get(Fact, facts[2].id)).status == "verified"
            assert (await db.get(Fact, facts[3].id)).status == "machine"  # D-5: machine stays machine

            # a later Workbench edit makes the attestation lapse
            await fact_verification_service.bulk_edit_facts(db, tenant_id, [{"fact_id": facts[3].id, "new_value": {"v": "47"}}], actor_id)
            doc = await _open(db, tenant_id, doc_id)
            assert _table(doc)["rows"][1]["status"] == rs.EDITED

            entries = (await db.execute(select(ReviewAuditEntry.action).where(ReviewAuditEntry.document_id == doc_id))).scalars().all()
            assert entries == ["verify_row"]
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_unverify_row_withdraws_only_its_own_confirmations():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row = _table(doc)["rows"][1]
            doc = await rs.set_verified(db, tenant_id, doc_id, actor_id, "records_officer", doc["version"], TABLE, row["id"], True)
            doc = await rs.set_verified(db, tenant_id, doc_id, actor_id, "records_officer", doc["version"], TABLE, row["id"], False)
            assert _table(doc)["rows"][1]["status"] == rs.MACHINE
            assert (await db.get(Fact, facts[2].id)).status == "in_review"
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_history_lists_who_when_old_new_for_one_cell():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, _ = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            row_id = _table(doc)["rows"][0]["id"]
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, 1, "12/8")
            doc = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, 1)
            doc = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, row_id, 0, "X")
            history = await rs.get_history(db, tenant_id, doc_id, TABLE, row_id, 1)
            assert [(h["action"], h["old_value"], h["new_value"]) for h in history] == [
                ("revert_cell", "12/8", "12/3"), ("edit_cell", "12/3", "12/8"),
            ]
            assert history[0]["user_id"] == str(actor_id) and history[0]["created_at"]
            assert _cell(doc, 0, 1)["history_count"] == 2
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_snapshot_keeps_the_ocr_value_even_after_a_prior_workbench_edit():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, facts = await _setup(db)
            await fact_verification_service.bulk_edit_facts(db, tenant_id, [{"fact_id": facts[1].id, "new_value": {"v": "12/4"}}], actor_id)
            doc = await _open(db, tenant_id, doc_id)
            cell = _cell(doc, 0, 1)
            assert cell["text"] == "12/4" and cell["original"] == "12/3" and cell["edited"]
            assert cell["changed_elsewhere"] and not cell["revertable"]
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_original_snapshot_and_review_audit_are_immutable():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id, _ = await _setup(db)
            doc = await _open(db, tenant_id, doc_id)
            await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", doc["version"], TABLE, _table(doc)["rows"][0]["id"], 0, "X")
            for sql in (
                "UPDATE doc_dg_review_originals SET builder = 'x' WHERE document_id = :d",
                "UPDATE doc_dg_review_audit SET action = 'x' WHERE document_id = :d",
                "DELETE FROM doc_dg_review_audit WHERE document_id = :d",
            ):
                with pytest.raises(Exception, match="append-only/immutable"):
                    async with db.begin_nested():
                        await db.execute(text(sql), {"d": doc_id})
        finally:
            await db.rollback()
