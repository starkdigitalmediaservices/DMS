"""TS3 — content-hash-keyed archive for raw OCR and VLM responses.

Not tenant-scoped by design: see doc_dg_ocr_archive/doc_dg_vlm_archive's
migration docstring. Every function here is best-effort from the
caller's point of view — a cache miss or a write failure just means
"go do the real OCR/VLM call," never a hard failure, consistent with
this pipeline's existing best-effort conventions (T22/T23/TS1/TS2 all
degrade the same way).
"""
import hashlib
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ocr_archive import OCRArchive
from app.models.vlm_archive import VLMArchive


def _has_failed_page(pages: Optional[List[dict]]) -> bool:
    return any(p.get("extraction_failed") for p in (pages or []))


# A failed page is usually a provider failure (quota exhausted, timeout,
# outage), not a property of the file. Caching it made the failure
# permanent: on 2026-09-23, 153 of 171 cached chandra results and 97 of
# 117 paddleocr results were failures -- mostly from a Datalab quota 403 --
# so re-uploading those files after the credits were topped up failed
# again without the provider ever being called. Failed results are now
# never served from, or written to, the cache.

async def get_cached_ocr(db: AsyncSession, content_hash: str, ocr_engine: str) -> Optional[List[dict]]:
    archived = await db.get(OCRArchive, {"content_hash": content_hash, "ocr_engine": ocr_engine})
    if archived is None or _has_failed_page(archived.pages):
        return None
    return archived.pages


async def record_ocr(db: AsyncSession, content_hash: str, ocr_engine: str, pages: List[dict]) -> None:
    if _has_failed_page(pages):
        return
    existing = await db.get(OCRArchive, {"content_hash": content_hash, "ocr_engine": ocr_engine})
    if existing:
        # Only a previously failed entry is replaced; a good one stays as it
        # was first recorded (the archive is also a record of what was read).
        if _has_failed_page(existing.pages):
            existing.pages = pages
            await db.flush()
        return
    db.add(OCRArchive(content_hash=content_hash, ocr_engine=ocr_engine, pages=pages))
    await db.flush()


def compute_vlm_cache_key(file_hash: str, page_number: int, prompt: str, vlm_provider: str) -> str:
    """vlm_provider is part of the key for the same reason ocr_engine is
    part of get_cached_ocr's lookup: without it, switching AI_VLM_PROVIDER
    (e.g. gemini -> chandra) silently keeps serving the OLD provider's
    cached response for any document already seen under that
    (file_hash, page_number, prompt) triple, defeating the switch."""
    canonical = f"{file_hash}:{page_number}:{prompt}:{vlm_provider}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_cached_vlm_response(db: AsyncSession, cache_key: str) -> Optional[str]:
    archived = await db.get(VLMArchive, cache_key)
    return archived.raw_response if archived else None


async def record_vlm_response(db: AsyncSession, cache_key: str, raw_response: str) -> None:
    existing = await db.get(VLMArchive, cache_key)
    if existing:
        return
    db.add(VLMArchive(cache_key=cache_key, raw_response=raw_response))
    await db.flush()


async def overwrite_vlm_response(db: AsyncSession, cache_key: str, raw_response: str) -> None:
    """Like record_vlm_response, but replaces an existing entry instead of
    leaving it in place. record_vlm_response's write-once behavior is
    correct for its normal caller (a fresh response for a key that's
    never been cached) but wrong for vlm_extraction's parse-retry path:
    a retry only happens because the cached response under this exact key
    was unparseable, so silently no-op'ing on "existing" would leave the
    bad response cached forever and every later run would keep retrying
    and discarding the same known-bad text."""
    existing = await db.get(VLMArchive, cache_key)
    if existing:
        existing.raw_response = raw_response
        await db.flush()
        return
    db.add(VLMArchive(cache_key=cache_key, raw_response=raw_response))
    await db.flush()
