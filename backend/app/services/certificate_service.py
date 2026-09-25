import hashlib
import io
from datetime import datetime, timezone
from typing import Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.services.audit_service import verify_chain_integrity, log_action

CONTENT_TYPE = "application/pdf"


async def generate_section63_certificate(
    db: AsyncSession, tenant_id: UUID, actor_id: UUID, document_id: UUID,
) -> Tuple[bytes, str, str]:
    """T65 — Section 63 certificate: hash value, algorithm name, dual
    signature blocks (docs/planning/build_design.txt Section 12/(h)). TEMPLATE ONLY —
    the wording has not been reviewed by legal counsel (assumption A3,
    still open per docs/planning/backlog.txt). Every certificate this generates carries
    a visible draft banner; it is a structural/technical placeholder, not
    an evidentiary instrument, until A3 clears."""
    if actor_id is None:
        raise ValueError("certificate generation requires an actor")

    doc_res = await db.execute(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    document = doc_res.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    version = None
    if document.current_version_id:
        v_res = await db.execute(
            select(DocumentVersion).where(DocumentVersion.id == document.current_version_id)
        )
        version = v_res.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=409, detail="Document has no current version to certify")

    # T63 tie-in: cite the tenant-wide audit chain's integrity status at
    # generation time — the same evidentiary link docs/planning/build_design.txt draws
    # between "audit chain" and "Section 63 certificate".
    chain_status = await verify_chain_integrity(db, tenant_id)

    content = _to_pdf_bytes(document, version, chain_status)
    content_hash = hashlib.sha256(content).hexdigest()
    filename = f"section63_certificate_DRAFT_{document_id}.pdf"

    await log_action(
        db, actor_id, tenant_id, "governance.certificate_generated",
        resource_type="document", resource_id=document_id,
        details={
            "document_hash_sha256": version.file_hash,
            "certificate_hash_sha256": content_hash,
            "audit_chain_valid_at_generation": chain_status["valid"],
            "template_status": "draft_pending_legal_review_A3",
        },
    )
    await db.commit()

    return content, filename, CONTENT_TYPE


def _to_pdf_bytes(document: Document, version: DocumentVersion, chain_status: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    banner_style = ParagraphStyle(
        "DraftBanner", parent=styles["Normal"], textColor=colors.red,
        fontSize=10, alignment=TA_CENTER, borderColor=colors.red, borderWidth=1, borderPadding=6,
    )
    story = []

    story.append(Paragraph(
        "DRAFT TEMPLATE &mdash; NOT A VALID SECTION 63 CERTIFICATE UNTIL LEGAL "
        "COUNSEL REVIEW IS COMPLETE (Assumption A3, open). Generated for "
        "structural/technical review only.",
        banner_style,
    ))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("Certificate under Section 63", styles["Title"]))
    story.append(Paragraph(f"Generated: {datetime.now(timezone.utc).isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Record identification", styles["Heading2"]))
    rows = [
        ["Document ID", str(document.id)],
        ["Title", document.title],
        ["Document type", document.doc_type or "—"],
        ["Version number", str(version.version_number)],
        ["Original filename", version.original_filename],
    ]
    t = Table(rows, colWidths=[5 * cm, 10 * cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Integrity", styles["Heading2"]))
    rows = [
        ["Hash value", version.file_hash],
        ["Algorithm name", "SHA-256"],
        ["Audit chain status", "Valid" if chain_status["valid"] else "BROKEN — see /governance/audit-integrity"],
        ["Audit events checked", str(chain_status["checked_count"])],
    ]
    t = Table(rows, colWidths=[5 * cm, 10 * cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
    ]))
    story.append(t)
    story.append(Spacer(1, 1 * cm))

    story.append(Paragraph("Signatures", styles["Heading2"]))
    story.append(Paragraph(
        "This certificate takes effect only once both signature blocks "
        "below are completed by the named roles.", styles["Normal"],
    ))
    story.append(Spacer(1, 0.4 * cm))
    sig_rows = [
        ["Records Officer", "Department Head"],
        ["Name: ______________________", "Name: ______________________"],
        ["Designation: ________________", "Designation: ________________"],
        ["Signature: __________________", "Signature: __________________"],
        ["Date: _______________________", "Date: _______________________"],
    ]
    t = Table(sig_rows, colWidths=[7.5 * cm, 7.5 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)

    doc.build(story)
    return buf.getvalue()
