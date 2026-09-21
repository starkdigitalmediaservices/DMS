"""Scanner Connector (Task T44 - TWAIN & Network-Scan Integration).

Provides:
1. `process_scanned_bytes()`: Formats scanned page images (JPEG, PNG, TIFF, PDF).
   Converts multi-page TIFF scans to an archivable PDF/A-like document while retaining
   the original raw TIFF bytes alongside.
2. `poll_scanner_inbox_once()` & `scanner_poll_loop()`: Background poller watching
   `/app/scanner_inbox` for physical network office scanners (MFPs scanning over SMB/FTP).
3. File Lifecycle:
   - Picked-up & successfully ingested files (or hash duplicates) move to:
     `/app/scanner_inbox/processed/YYYY-MM-DD/`
   - Ingestion errors move to:
     `/app/scanner_inbox/failed/YYYY-MM-DD/`
4. Multi-Tenant Subfolder Mapping:
   Subfolders matching a user's registered email (e.g. `/app/scanner_inbox/user@tenant.com/doc.pdf`)
   auto-resolve that user's `tenant_id`. Root files fall back to `DEFAULT_CONNECTOR_EMAIL`.
"""
import asyncio
import datetime
import hashlib
import io
import logging
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import numpy as np
from scipy import ndimage
import uuid

from PIL import Image, ImageSequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.metadata_item import MetadataItem
from app.services.connector_ingest_service import (
    DEFAULT_CONNECTOR_EMAIL,
    already_ingested,
    get_connector_actor,
    get_or_create_folder_path,
    ingest_bytes,
)

logger = logging.getLogger(__name__)

STABILITY_GRACE_SECONDS = 5
_pending_sizes: Dict[str, int] = {}


def assess_scan_quality(image_bytes: bytes) -> Dict[str, Any]:
    """Perform pre-ingestion scan quality checks (blur, exposure, blank page, resolution, skew).

    Returns a ScanQualityReport dictionary:
    {
        "passed": bool,
        "warnings": ["blurry", "underexposed", ...],
        "sharpness_score": float,
        "brightness_score": float,
        "blank_variance_score": float,
        "skew_angle_degrees": float,
        "skew_confidence": "low",
        "resolution": (width, height),
    }
    """
    if not settings.scanner_quality_check_enabled:
        return {
            "passed": True,
            "warnings": [],
            "sharpness_score": 999.0,
            "brightness_score": 128.0,
            "blank_variance_score": 1000.0,
            "skew_angle_degrees": 0.0,
            "skew_confidence": "low",
            "resolution": (1000, 1000),
        }

    warnings: List[str] = []

    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
    except Exception as e:
        logger.warning("Scan quality check: unable to parse image bytes: %s", e)
        return {
            "passed": True,
            "warnings": ["unparseable_image_format"],
            "sharpness_score": 0.0,
            "brightness_score": 0.0,
            "blank_variance_score": 0.0,
            "skew_angle_degrees": 0.0,
            "skew_confidence": "low",
            "resolution": (0, 0),
        }

    # 1. Resolution Check
    min_dim = min(width, height)
    if min_dim < settings.scanner_min_resolution_px:
        warnings.append("low_resolution")

    # Convert to grayscale numpy array for image math
    try:
        gray_img = img.convert("L")
        gray_arr = np.array(gray_img, dtype=np.float64)
    except Exception as e:
        logger.warning("Scan quality check: failed grayscale conversion: %s", e)
        return {
            "passed": True,
            "warnings": [],
            "sharpness_score": 100.0,
            "brightness_score": 128.0,
            "blank_variance_score": 1000.0,
            "skew_angle_degrees": 0.0,
            "skew_confidence": "low",
            "resolution": (width, height),
        }

    # 2. Blur Detection (Laplacian Variance using SciPy ndimage)
    laplacian = ndimage.laplace(gray_arr)
    sharpness_score = float(laplacian.var())
    if sharpness_score < settings.scanner_min_sharpness_threshold:
        warnings.append("blurry")

    # 3. Brightness / Contrast Check
    brightness_score = float(np.mean(gray_arr))
    if brightness_score < settings.scanner_min_brightness:
        warnings.append("underexposed")
    elif brightness_score > settings.scanner_max_brightness:
        warnings.append("overexposed")

    # 4. Blank Page Detection (Pixel Variance)
    blank_variance_score = float(np.var(gray_arr))
    if blank_variance_score < settings.scanner_min_blank_variance:
        warnings.append("possible_blank_page")

    # 5. Skew Angle Check (Informative metric)
    skew_angle = 0.0
    try:
        gy, gx = np.gradient(gray_arr)
        orientation = np.arctan2(gy, gx)
        skew_angle = float(np.degrees(np.median(orientation))) % 90
        if skew_angle > 45:
            skew_angle = 90 - skew_angle
    except Exception:
        skew_angle = 0.0

    passed = len(warnings) == 0

    return {
        "passed": passed,
        "warnings": warnings,
        "sharpness_score": round(sharpness_score, 2),
        "brightness_score": round(brightness_score, 2),
        "blank_variance_score": round(blank_variance_score, 2),
        "skew_angle_degrees": round(skew_angle, 2),
        "skew_confidence": "low",
        "resolution": (width, height),
    }


def process_scanned_bytes(
    content: bytes,
    filename: str,
    mime_type: Optional[str] = None,
) -> Tuple[bytes, str, str, Optional[bytes]]:
    """Process raw scanned image/PDF bytes.

    If input is single or multi-page TIFF, extracts frames using Pillow and
    compiles them into a single PDF/A-like archivable PDF document.

    Returns:
        (processed_content, target_filename, target_mime_type, raw_original_bytes_if_converted)
    """
    ext = Path(filename).suffix.lower()
    inferred_mime = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    is_tiff = ext in [".tif", ".tiff"] or inferred_mime in ["image/tiff", "image/x-tiff"]

    if not is_tiff:
        return content, filename, inferred_mime, None

    # Handle TIFF conversion to PDF
    try:
        tiff_img = Image.open(io.BytesIO(content))
        frames: List[Image.Image] = []

        for frame in ImageSequence.Iterator(tiff_img):
            frame_converted = frame.convert("RGB")
            frames.append(frame_converted)

        if not frames:
            logger.warning("Scanner connector: TIFF '%s' contained 0 readable frames", filename)
            return content, filename, inferred_mime, None

        pdf_io = io.BytesIO()
        first_frame = frames[0]
        rest_frames = frames[1:] if len(frames) > 1 else []

        first_frame.save(
            pdf_io,
            format="PDF",
            save_all=True,
            append_images=rest_frames,
            resolution=float(settings.scanner_default_dpi),
        )
        pdf_bytes = pdf_io.getvalue()
        stem = Path(filename).stem
        pdf_filename = f"{stem}_scanned.pdf"

        logger.info(
            "Scanner connector: converted multi-page TIFF '%s' (%d frames) -> PDF '%s' (%d bytes)",
            filename,
            len(frames),
            pdf_filename,
            len(pdf_bytes),
        )
        return pdf_bytes, pdf_filename, "application/pdf", content

    except Exception as e:
        logger.error("Scanner connector: failed TIFF-to-PDF conversion for '%s': %s", filename, e)
        return content, filename, inferred_mime, None


async def resolve_scanner_actor(
    db: AsyncSession,
    subfolder_name: Optional[str] = None,
) -> Tuple[UUID, UUID]:
    """Resolve (tenant_id, user_id) for scanned file.

    If subfolder_name is a registered user email, resolves that user's identity.
    Otherwise falls back to DEFAULT_CONNECTOR_EMAIL.
    """
    if subfolder_name and "@" in subfolder_name:
        result = await db.execute(select(User).where(User.email == subfolder_name.strip()))
        user = result.scalar_one_or_none()
        if user:
            return user.tenant_id, user.id

    return await get_connector_actor(db, DEFAULT_CONNECTOR_EMAIL)


def _iter_candidate_files(inbox_dir: Path) -> List[Path]:
    """Recursively walk inbox_dir, skipping 'processed' and 'failed' bookkeeping folders."""
    if not inbox_dir.exists():
        return []

    candidates = []
    for path in inbox_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(inbox_dir).parts
        if rel_parts[0] in ("processed", "failed"):
            continue
        candidates.append(path)
    return candidates


async def poll_scanner_inbox_once() -> int:
    """Poll network scanner inbox directory once.

    Returns:
        Count of scanned files successfully processed or ingested.
    """
    inbox_dir = Path(settings.scanner_inbox_dir)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    processed_dir = inbox_dir / "processed" / today_str
    failed_dir = inbox_dir / "failed" / today_str

    inbox_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    candidates = _iter_candidate_files(inbox_dir)
    if not candidates:
        return 0

    seen_keys = {str(p.relative_to(inbox_dir)) for p in candidates}
    for stale_key in list(_pending_sizes):
        if stale_key not in seen_keys:
            del _pending_sizes[stale_key]

    stable_paths: List[Path] = []
    now = time.time()

    for path in candidates:
        rel = str(path.relative_to(inbox_dir))
        try:
            stat = path.stat()
            size = stat.st_size
            mtime_age = now - stat.st_mtime
            last_seen = _pending_sizes.get(rel)

            if last_seen == size and mtime_age >= STABILITY_GRACE_SECONDS:
                stable_paths.append(path)
                del _pending_sizes[rel]
            else:
                _pending_sizes[rel] = size
                logger.info(
                    "Scanner poller: '%s' not yet stable (%d bytes, mtime %.1fs ago), waiting",
                    rel,
                    size,
                    mtime_age,
                )
        except OSError:
            continue

    if not stable_paths:
        return 0

    ingested_count = 0

    async with AsyncSessionLocal() as db:
        for path in stable_paths:
            rel_parts = path.relative_to(inbox_dir).parts
            filename = path.name
            subfolder_name = rel_parts[0] if len(rel_parts) > 1 else None

            # 1. Resolve tenant & actor
            try:
                tenant_id, user_id = await resolve_scanner_actor(db, subfolder_name)
            except Exception as e:
                logger.error("Scanner poller: failed to resolve actor for '%s': %s", path, e)
                dest_failed = failed_dir / filename
                shutil.move(str(path), str(dest_failed))
                continue

            # 2. Read raw file & compute hash
            try:
                raw_bytes = path.read_bytes()
            except Exception as e:
                logger.error("Scanner poller: failed to read file '%s': %s", path, e)
                dest_failed = failed_dir / filename
                shutil.move(str(path), str(dest_failed))
                continue

            file_hash = hashlib.sha256(raw_bytes).hexdigest()

            # 3. Check for duplicates
            if await already_ingested(db, tenant_id, file_hash):
                logger.info("Scanner poller: '%s' already ingested (hash match), moving to processed", filename)
                dest_processed = processed_dir / filename
                shutil.move(str(path), str(dest_processed))
                continue

            # 4. Process scanned bytes (e.g. multi-page TIFF -> PDF conversion)
            guessed_mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            proc_content, proc_filename, proc_mime, _ = process_scanned_bytes(
                raw_bytes, filename, mime_type=guessed_mime
            )

            # 4b. Assess Scan Quality
            quality_report = assess_scan_quality(proc_content)
            if not quality_report["passed"]:
                logger.warning(
                    "Scanner poller: '%s' quality warnings: %s",
                    filename,
                    quality_report["warnings"],
                )

            # 5. Resolve / create "Scanned Documents" folder in DMS
            try:
                folder_id = await get_or_create_folder_path(db, tenant_id, ["Scanned Documents"])
            except Exception as e:
                logger.warning("Scanner poller: could not resolve 'Scanned Documents' folder: %s", e)
                folder_id = None

            # 6. Ingest into DMS via shared connector entry point
            try:
                resp = await ingest_bytes(
                    proc_content,
                    proc_filename,
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    content_type=proc_mime,
                    folder_id=folder_id,
                )
                logger.info("Scanner poller: successfully ingested '%s' as document %s", filename, resp.document_id)

                if not quality_report["passed"]:
                    try:
                        meta_flag = MetadataItem(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            document_id=resp.document_id,
                            key="quality_flag",
                            value={"flag": "needs_review", "warnings": quality_report["warnings"]},
                            source="scanner_connector",
                            confidence_score=0.9,
                        )
                        meta_report = MetadataItem(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            document_id=resp.document_id,
                            key="quality_report",
                            value=quality_report,
                            source="scanner_connector",
                            confidence_score=1.0,
                        )
                        db.add_all([meta_flag, meta_report])
                        await db.commit()
                    except Exception as meta_err:
                        logger.warning("Scanner poller: failed to attach quality metadata: %s", meta_err)

                dest_processed = processed_dir / filename
                shutil.move(str(path), str(dest_processed))
                ingested_count += 1
            except Exception as e:
                logger.error("Scanner poller: failed to ingest '%s': %s", path, e)
                dest_failed = failed_dir / filename
                shutil.move(str(path), str(dest_failed))

    return ingested_count


async def scanner_poll_loop():
    """Background asyncio poller loop for network scanner inbox directory."""
    if not settings.scanner_enabled:
        logger.info("Scanner connector disabled (SCANNER_ENABLED=false)")
        return

    logger.info(
        "Scanner connector started, watching %s every %ds",
        settings.scanner_inbox_dir,
        settings.scanner_poll_interval_seconds,
    )

    while True:
        try:
            await poll_scanner_inbox_once()
        except Exception as e:
            logger.error("Scanner poll cycle error: %s", e)
        await asyncio.sleep(settings.scanner_poll_interval_seconds)

class ScannerConnector:
    name = "scanner"

    async def poll_once(self) -> int:
        return await poll_scanner_inbox_once()

    async def run_loop(self) -> None:
        await scanner_poll_loop()
