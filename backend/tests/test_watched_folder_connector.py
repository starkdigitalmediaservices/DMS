"""T44 — the watched-folder connector ingests anything dropped into the
directory this module watches, including a network MFP configured to write
there by its own "Scan to Network Folder" feature. Zero
coverage existed for its actual poll/ingest logic before this file --
test_connector_contract.py only covers the thin Protocol wrapper.

Real temp directories, real DB writes (AsyncSessionLocal, matching this
project's live-verification convention), get_connector_actor monkeypatched
to a throwaway tenant/user so this never depends on any real account
existing.

Also closed a real bug found while writing this file: ingest_bytes() used
to re-resolve the connector's tenant/user internally (its own
get_connector_actor() call, via an actor_email default) instead of using
the tenant/user its own caller had already resolved -- every real ingest
was looking the actor up twice, redundant, and it meant patching only
poll_source_once's resolution (not ingest_bytes' separate internal one)
silently wrote real documents into the real production tenant during an
early version of these tests. Fixed in connector_ingest_service.py:
ingest_bytes now takes tenant_id/user_id as required params, passed
through by every caller (watched-folder, SFTP, email-in, the inbound
webhook) instead of re-derived. test_ingest_bytes_never_re_resolves_the_actor
below proves it stays fixed."""
import os
import time
import uuid

import pytest

from app.database import AsyncSessionLocal
from sqlalchemy import select, delete, update, text
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.folder import Folder
from app.services.auth_service import hash_password
import app.services.watched_folder_connector as wfc
import app.services.connector_ingest_service as cis


def _make_source(tmp_path, name="test_source"):
    watch_dir = tmp_path / "watch"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    return wfc.WatchSource(name=name, watch_dir=watch_dir, processed_dir=processed_dir, failed_dir=failed_dir)


def _backdate(path, seconds_ago):
    """Simulate a file that finished writing well before this poll --
    real bytes on disk, just an mtime set in the past, so the stability
    check (STABILITY_GRACE_SECONDS) passes on the very first poll instead
    of every test needing a real multi-second sleep."""
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


@pytest.fixture
async def throwaway_actor():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=tenant_id, name=f"T44 Test {uuid.uuid4().hex[:6]}"))
        await db.flush()
        db.add(User(
            id=user_id, tenant_id=tenant_id, email=f"t44_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("x"), role=UserRole.it_admin,
        ))
        await db.commit()
    yield tenant_id, user_id
    async with AsyncSessionLocal() as db:
        await db.execute(update(Document).where(Document.tenant_id == tenant_id).values(current_version_id=None))
        await db.execute(delete(DocumentVersion).where(
            DocumentVersion.document_id.in_(select(Document.id).where(Document.tenant_id == tenant_id))
        ))
        await db.execute(delete(Document).where(Document.tenant_id == tenant_id))
        await db.execute(delete(Folder).where(Folder.tenant_id == tenant_id))
        await db.execute(text("DELETE FROM billing_dg_subscription WHERE tenant_id = :t"), {"t": str(tenant_id)})
        await db.execute(delete(User).where(User.id == user_id))
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


def _patch_actor(monkeypatch, tenant_id, user_id):
    """poll_source_once calls get_connector_actor once and now passes the
    result straight through to ingest_bytes() (see connector_ingest_service.py
    -- ingest_bytes used to re-resolve the actor a second time internally,
    fixed below this file's own bug fix). Patching this one binding is
    correct and sufficient post-fix; test_ingest_bytes_never_re_resolves_
    the_actor proves the internal re-resolution stays gone."""
    async def fake_get_connector_actor(db, email=None):
        return tenant_id, user_id
    monkeypatch.setattr(wfc, "get_connector_actor", fake_get_connector_actor)


@pytest.mark.asyncio
async def test_empty_watch_dir_is_a_noop(tmp_path, monkeypatch, throwaway_actor):
    tenant_id, user_id = throwaway_actor
    _patch_actor(monkeypatch, tenant_id, user_id)
    source = _make_source(tmp_path)
    assert await wfc.poll_source_once(source) == 0


@pytest.mark.asyncio
async def test_freshly_written_file_is_not_ingested_yet(tmp_path, monkeypatch, throwaway_actor):
    """A scanner's file transfer can still be mid-write on the exact poll
    boundary -- ingesting too early would grab a truncated scan. A
    just-written file (real 'now' mtime) must wait, not be picked up on
    the very first poll."""
    tenant_id, user_id = throwaway_actor
    _patch_actor(monkeypatch, tenant_id, user_id)
    source = _make_source(tmp_path)
    (source.watch_dir).mkdir(parents=True)
    scan_file = source.watch_dir / "scan_0001.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fresh scan, still mid-transfer")

    ingested = await wfc.poll_source_once(source)
    assert ingested == 0
    assert scan_file.exists()  # untouched, still sitting in the watch dir


@pytest.mark.asyncio
async def test_stable_scan_gets_ingested_and_moved_to_processed(tmp_path, monkeypatch, throwaway_actor):
    tenant_id, user_id = throwaway_actor
    _patch_actor(monkeypatch, tenant_id, user_id)
    source = _make_source(tmp_path)
    source.watch_dir.mkdir(parents=True)
    scan_file = source.watch_dir / "scan_0002.pdf"
    unique = uuid.uuid4().hex
    scan_file.write_bytes(f"%PDF-1.4 completed scan {unique}".encode())
    _backdate(scan_file, wfc.STABILITY_GRACE_SECONDS + 5)

    # The real poll loop needs two cycles minimum: the first just records
    # the size it saw (a genuinely fresh drop can't be told apart from a
    # stable one on a single poll), the second confirms it's unchanged.
    # Backdating the mtime above means this second call already clears the
    # age check, matching what the second of two real 5-second-apart polls
    # would see for a scan that finished writing well before either of them.
    assert await wfc.poll_source_once(source) == 0
    ingested = await wfc.poll_source_once(source)
    assert ingested == 1
    assert not scan_file.exists()
    assert (source.processed_dir / "scan_0002.pdf").exists()

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Document).where(Document.tenant_id == tenant_id))
        docs = res.scalars().all()
        assert len(docs) == 1
        assert docs[0].title == "scan_0002.pdf"


@pytest.mark.asyncio
async def test_scan_dropped_in_a_subfolder_mirrors_as_a_real_dms_folder(tmp_path, monkeypatch, throwaway_actor):
    """A real MFP is often configured to scan into a per-department or
    per-user subfolder on the share (e.g. 'Registry/2026-09'). That
    structure should show up as a real, identically-named folder in DMS,
    not get flattened."""
    tenant_id, user_id = throwaway_actor
    _patch_actor(monkeypatch, tenant_id, user_id)
    source = _make_source(tmp_path)
    subdir = source.watch_dir / "Registry" / "2026-09"
    subdir.mkdir(parents=True)
    scan_file = subdir / "scan_0003.pdf"
    unique = uuid.uuid4().hex
    scan_file.write_bytes(f"%PDF-1.4 registry scan {unique}".encode())
    _backdate(scan_file, wfc.STABILITY_GRACE_SECONDS + 5)

    assert await wfc.poll_source_once(source) == 0  # first poll: registers the size
    ingested = await wfc.poll_source_once(source)   # second: confirms unchanged, ingests
    assert ingested == 1

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Document).where(Document.tenant_id == tenant_id))
        doc = res.scalar_one()
        assert doc.folder_id is not None

        folder = await db.get(Folder, doc.folder_id)
        assert folder.name == "2026-09"
        parent = await db.get(Folder, folder.parent_id)
        assert parent.name == "Registry"


@pytest.mark.asyncio
async def test_duplicate_scan_is_skipped_but_still_moved_out_of_the_watch_dir(tmp_path, monkeypatch, throwaway_actor):
    """A scanner or a person can drop the same file twice (a re-scan, a
    retry after a jam). The second copy must not create a second
    document, but it also must not sit in the watch dir forever."""
    tenant_id, user_id = throwaway_actor
    _patch_actor(monkeypatch, tenant_id, user_id)
    source = _make_source(tmp_path)
    source.watch_dir.mkdir(parents=True)
    content = f"%PDF-1.4 duplicate test {uuid.uuid4().hex}".encode()

    first = source.watch_dir / "scan_0004.pdf"
    first.write_bytes(content)
    _backdate(first, wfc.STABILITY_GRACE_SECONDS + 5)
    assert await wfc.poll_source_once(source) == 0  # registers
    assert await wfc.poll_source_once(source) == 1  # confirms + ingests

    second = source.watch_dir / "scan_0004_retry.pdf"
    second.write_bytes(content)
    _backdate(second, wfc.STABILITY_GRACE_SECONDS + 5)
    assert await wfc.poll_source_once(source) == 0  # registers
    ingested = await wfc.poll_source_once(source)   # confirms; same hash as `first`
    assert ingested == 0  # skipped as a duplicate, not counted as a new ingest
    assert not second.exists()
    assert (source.processed_dir / "scan_0004_retry.pdf").exists()

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Document).where(Document.tenant_id == tenant_id))
        assert len(res.scalars().all()) == 1


@pytest.mark.asyncio
async def test_ingest_bytes_never_re_resolves_the_actor(monkeypatch, throwaway_actor):
    """Regression test for the real bug this file's own docstring
    describes: ingest_bytes() must use exactly the tenant_id/user_id its
    caller passes in, never look the actor up again on its own. Sabotage
    get_connector_actor to return an obviously-wrong identity, call
    ingest_bytes with the real throwaway actor directly, and confirm the
    resulting document belongs to the explicit actor -- if ingest_bytes
    ever re-gains an internal resolution, this fails loudly instead of
    silently writing to whatever get_connector_actor happens to return."""
    tenant_id, user_id = throwaway_actor

    async def sabotaged_get_connector_actor(db, email=None):
        raise AssertionError(
            "ingest_bytes must not call get_connector_actor internally -- "
            "the caller already resolved and passed the actor explicitly"
        )
    monkeypatch.setattr(cis, "get_connector_actor", sabotaged_get_connector_actor)

    unique = uuid.uuid4().hex
    async with AsyncSessionLocal() as db:
        resp = await cis.ingest_bytes(
            f"%PDF-1.4 direct ingest {unique}".encode(), "direct.pdf", db, tenant_id, user_id,
            content_type="application/pdf",
        )

    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, resp.document_id)
        assert doc.tenant_id == tenant_id
