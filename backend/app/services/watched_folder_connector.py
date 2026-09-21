"""Watched folder connector.

Polls one or more local directories (recursively) for new files and ingests
each one through the shared connector entry point
(connector_ingest_service.ingest_bytes), same as the FTP/SFTP and email-in
adapters. A file's subfolder path relative to its watch root is mirrored as
a real, identically-nested DMS folder (via
connector_ingest_service.get_or_create_folder_path), the same way a browser
folder-drop creates real folders.

Multiple independent watch sources are supported (WATCH_SOURCES below) so a
real, already-in-use folder (e.g. a user's own local sync folder) can be
watched directly — auto-sync, Google-Drive/OneDrive style — without routing
through a dedicated test/demo inbox. Each source has its own
processed/failed bookkeeping location, which does not have to live inside
the watched folder itself (so a user's real folder doesn't get cluttered
with connector housekeeping subfolders).

Demo scope: fixed set of watch sources (configured in code, not a UI),
fixed poll interval, runs as a background task in the backend process.
Production scope (per-tenant watch paths, a UI to add/remove watched
folders, retry/alerting) is backlog T40-T42.
"""
import asyncio
import hashlib
import logging
import mimetypes
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..database import AsyncSessionLocal
from .connector_ingest_service import ingest_bytes, get_connector_actor, already_ingested, get_or_create_folder_path

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5

# A file is only ingested once its size matches what we saw on the previous
# poll AND its mtime hasn't been touched for at least STABILITY_GRACE_SECONDS.
# Size-match alone isn't enough: a writer that pauses mid-write (slow copy,
# network transfer) can look "unchanged" across two poll boundaries that both
# land inside the pause, well before the write actually finishes. Requiring
# a longer mtime-quiet period closes that gap.
STABILITY_GRACE_SECONDS = 2 * POLL_INTERVAL_SECONDS


@dataclass(frozen=True)
class WatchSource:
    name: str
    watch_dir: Path
    processed_dir: Path
    failed_dir: Path


WATCH_SOURCES = [
    # Original test/demo inbox: bookkeeping lives inside the watched folder itself.
    WatchSource(
        name="default",
        watch_dir=Path("/app/watched_inbox"),
        processed_dir=Path("/app/watched_inbox/processed"),
        failed_dir=Path("/app/watched_inbox/failed"),
    ),
    # A real, already-in-use local folder: bookkeeping is kept OUT of it (in a
    # separate mounted state volume) so the user's own folder stays clean —
    # only their real files ever appear there, no connector-created subfolders.
    WatchSource(
        name="stark_drive",
        watch_dir=Path("/app/stark_drive_inbox"),
        processed_dir=Path("/app/_state/stark_drive/processed"),
        failed_dir=Path("/app/_state/stark_drive/failed"),
    ),
]

# Keyed by f"{source.name}:{relative_path}" so identical relative paths in
# different sources are tracked independently.
_pending_sizes: dict[str, int] = {}


def _reserved_top_level_names(source: WatchSource) -> set[str]:
    """Top-level subfolder names to skip when scanning watch_dir — only
    relevant when processed_dir/failed_dir happen to live inside watch_dir."""
    names = set()
    for special_dir in (source.processed_dir, source.failed_dir):
        try:
            rel = special_dir.relative_to(source.watch_dir)
        except ValueError:
            continue  # bookkeeping dir lives outside watch_dir, nothing to reserve
        names.add(rel.parts[0])
    return names


def _iter_candidate_files(source: WatchSource):
    """Recursively walk source.watch_dir, skipping its own bookkeeping subtree (if any)."""
    if not source.watch_dir.exists():
        return
    reserved = _reserved_top_level_names(source)
    for path in source.watch_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(source.watch_dir).parts
        if rel_parts[0] in reserved:
            continue
        yield path


async def poll_source_once(source: WatchSource) -> int:
    """Scan one watch source (recursively) once. Returns the count of files ingested."""
    source.watch_dir.mkdir(parents=True, exist_ok=True)
    source.processed_dir.mkdir(parents=True, exist_ok=True)
    source.failed_dir.mkdir(parents=True, exist_ok=True)

    candidates = list(_iter_candidate_files(source))
    if not candidates:
        return 0

    def state_key(rel: str) -> str:
        return f"{source.name}:{rel}"

    seen_keys = {state_key(str(p.relative_to(source.watch_dir))) for p in candidates}
    for stale_key in list(_pending_sizes):
        if stale_key.startswith(f"{source.name}:") and stale_key not in seen_keys:
            del _pending_sizes[stale_key]  # file vanished before stabilizing; drop tracking

    stable_paths = []
    for path in candidates:
        rel = str(path.relative_to(source.watch_dir))
        key = state_key(rel)
        stat = path.stat()
        size = stat.st_size
        mtime_age = time.time() - stat.st_mtime
        last_seen = _pending_sizes.get(key)

        if last_seen == size and mtime_age >= STABILITY_GRACE_SECONDS:
            stable_paths.append(path)
            del _pending_sizes[key]
        else:
            _pending_sizes[key] = size
            logger.info(
                "Watched folder [%s]: '%s' not yet stable (%d bytes, mtime %.1fs ago), waiting",
                source.name, rel, size, mtime_age,
            )

    if not stable_paths:
        return 0

    ingested = 0
    async with AsyncSessionLocal() as db:
        tenant_id, user_id = await get_connector_actor(db)
        folder_cache: dict = {}

        for path in stable_paths:
            rel_parts = path.relative_to(source.watch_dir).parts
            subfolder_segments = list(rel_parts[:-1])
            dest_processed = source.processed_dir.joinpath(*subfolder_segments)
            dest_failed = source.failed_dir.joinpath(*subfolder_segments)

            content = path.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()

            if await already_ingested(db, tenant_id, file_hash):
                logger.info("Watched folder [%s]: '%s' already ingested (hash match), skipping", source.name, path.name)
                dest_processed.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest_processed / path.name))
                continue

            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                folder_id = await get_or_create_folder_path(db, tenant_id, subfolder_segments, folder_cache) \
                    if subfolder_segments else None
                resp = await ingest_bytes(content, path.name, db, tenant_id, user_id, content_type=content_type, folder_id=folder_id)
                # rel_parts is a path tuple; "/".join it so the log reads
                # 'Vendors/invoice.pdf' rather than the Python repr
                # "('Vendors', 'invoice.pdf')".
                logger.info(
                    "Watched folder [%s]: ingested '%s' as document %s",
                    source.name, "/".join(rel_parts), resp.document_id,
                )
                dest_processed.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest_processed / path.name))
                ingested += 1
            except Exception as e:
                logger.error("Watched folder [%s]: failed to ingest '%s': %s", source.name, path, e)
                dest_failed.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest_failed / path.name))

    _cleanup_empty_dirs(source)
    return ingested


def _cleanup_empty_dirs(source: WatchSource) -> None:
    """Remove now-empty subfolders left behind after their files were moved out,
    so a dropped folder doesn't leave a hollow, empty shell sitting in the watched
    folder forever. Bottom-up so a chain of nested empty folders collapses fully
    in a single pass."""
    if not source.watch_dir.exists():
        return
    reserved = _reserved_top_level_names(source)

    def _walk_dirs_bottom_up(d: Path):
        for child in d.iterdir():
            if child.is_dir():
                yield from _walk_dirs_bottom_up(child)
                yield child

    for d in _walk_dirs_bottom_up(source.watch_dir):
        rel_parts = d.relative_to(source.watch_dir).parts
        if rel_parts[0] in reserved:
            continue  # never touch the processed/failed bookkeeping trees
        try:
            d.rmdir()  # only succeeds if truly empty
        except OSError:
            pass  # still has something in it, leave it alone


async def poll_watched_folder_once() -> int:
    """Poll every configured watch source once. Returns the total count ingested."""
    total = 0
    for source in WATCH_SOURCES:
        try:
            total += await poll_source_once(source)
        except Exception as e:
            logger.error("Watched folder [%s]: poll cycle failed: %s", source.name, e)
    return total


async def watch_folder_loop():
    for source in WATCH_SOURCES:
        logger.info("Watched folder connector started, watching [%s] %s every %ds", source.name, source.watch_dir, POLL_INTERVAL_SECONDS)
    while True:
        try:
            await poll_watched_folder_once()
        except Exception as e:
            logger.error("Watched folder poll cycle failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


class WatchedFolderConnector:
    """T40 — Connector-protocol wrapper around this module's functions."""

    name = "watched_folder"

    async def poll_once(self) -> int:
        return await poll_watched_folder_once()

    async def run_loop(self) -> None:
        await watch_folder_loop()
