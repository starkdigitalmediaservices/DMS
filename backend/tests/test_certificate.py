import io
import pytest
import uuid
import pdfplumber
from fastapi import HTTPException

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.services.certificate_service import generate_section63_certificate


@pytest.mark.asyncio
async def test_generate_certificate_includes_hash_algorithm_and_signatures():
    """T65 — certificate carries hash value, algorithm name, and dual
    signature blocks (docs/planning/build_design.txt Section 12/(h))."""
    async with AsyncSessionLocal() as db:
        try:
            tenant_id = uuid.uuid4()
            user_id = uuid.uuid4()
            document_id = uuid.uuid4()
            version_id = uuid.uuid4()

            tenant = Tenant(id=tenant_id, name=f"Cert Tenant {uuid.uuid4().hex[:6]}")
            user = User(id=user_id, tenant_id=tenant_id, email=f"cert_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw")
            db.add_all([tenant, user])
            await db.flush()

            document = Document(id=document_id, tenant_id=tenant_id, created_by=user_id, title="Test Register Page", status="processed")
            db.add(document)
            await db.flush()

            version = DocumentVersion(
                id=version_id, tenant_id=tenant_id, document_id=document_id, s3_path="s3://bucket/key",
                version_number=1, file_hash="a" * 64, file_size_bytes=1024,
                original_filename="test_register.pdf", uploaded_by=user_id,
            )
            db.add(version)
            await db.flush()
            document.current_version_id = version_id
            await db.commit()

            content, filename, content_type = await generate_section63_certificate(
                db, tenant_id, user_id, document_id,
            )

            assert content_type == "application/pdf"
            assert content[:4] == b"%PDF"
            assert "DRAFT" in filename

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            assert "a" * 64 in text  # the hash value
            assert "SHA-256" in text
            assert "Records Officer" in text
            assert "Department Head" in text
            assert "DRAFT" in text
            assert "A3" in text
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_generate_certificate_requires_actor():
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValueError):
            await generate_section63_certificate(db, uuid.uuid4(), None, uuid.uuid4())


@pytest.mark.asyncio
async def test_generate_certificate_404_for_missing_document():
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await generate_section63_certificate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_generate_certificate_409_when_no_current_version():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id = uuid.uuid4()
            user_id = uuid.uuid4()
            document_id = uuid.uuid4()

            tenant = Tenant(id=tenant_id, name=f"Cert Tenant {uuid.uuid4().hex[:6]}")
            user = User(id=user_id, tenant_id=tenant_id, email=f"cert_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw")
            db.add_all([tenant, user])
            await db.flush()

            document = Document(id=document_id, tenant_id=tenant_id, created_by=user_id, title="No Version Doc", status="pending")
            db.add(document)
            await db.commit()

            with pytest.raises(HTTPException) as exc:
                await generate_section63_certificate(db, tenant_id, user_id, document_id)
            assert exc.value.status_code == 409
        finally:
            await db.rollback()
            await db.close()
