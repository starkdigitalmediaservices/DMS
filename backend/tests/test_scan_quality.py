"""Scan quality check (app/services/scan_quality_service.py).

Restored 2026-09-23 after the scanner-connector removal (38e8229) deleted
the module it lived in and ingestion stopped quality-checking uploads, so
"Needs Review" quietly stopped receiving anything. These are the original
threshold tests, plus the wiring into the worker that went missing.
"""
import io
import inspect

from PIL import Image

from app.config import settings
from app.services.scan_quality_service import assess_scan_quality, is_image


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_sharp_clean_image_passes():
    img = Image.new("L", (1000, 1000), color=230)
    for y in range(50, 950, 30):
        for x in range(50, 950):
            if (x // 8) % 2 == 0:
                img.putpixel((x, y), 20)
    report = assess_scan_quality(_jpeg(img))
    assert report["passed"] is True
    assert report["warnings"] == []
    assert report["resolution"] == (1000, 1000)
    assert report["sharpness_score"] > settings.scan_quality_min_sharpness


def test_blurry_image_is_flagged():
    report = assess_scan_quality(_jpeg(Image.new("L", (1000, 1000), color=128)))
    assert "blurry" in report["warnings"] and report["passed"] is False


def test_dark_image_is_flagged():
    report = assess_scan_quality(_jpeg(Image.new("L", (1000, 1000), color=10)))
    assert "underexposed" in report["warnings"] and report["passed"] is False


def test_blank_page_is_flagged():
    report = assess_scan_quality(_jpeg(Image.new("L", (1000, 1000), color=255)))
    assert "possible_blank_page" in report["warnings"] and report["passed"] is False


def test_low_resolution_is_flagged():
    report = assess_scan_quality(_jpeg(Image.new("L", (300, 400), color=200)))
    assert "low_resolution" in report["warnings"]


def test_unparseable_bytes_pass_rather_than_block():
    report = assess_scan_quality(b"not an image")
    assert report["passed"] is True and report["warnings"] == ["unparseable_image_format"]


def test_disabled_check_always_passes(monkeypatch):
    monkeypatch.setattr(settings, "scan_quality_check_enabled", False)
    assert assess_scan_quality(_jpeg(Image.new("L", (100, 100), color=0)))["passed"] is True


def test_image_detection_by_extension_and_magic():
    assert is_image(".jpg", b"")
    assert is_image(".bin", b"\x89PNG\r\n")
    assert not is_image(".pdf", b"%PDF-1.7")


def test_worker_runs_the_check_and_writes_the_flag():
    """The regression itself: ingestion must call the check and persist a
    failing result as quality_flag, which is what "Needs Review" reads."""
    from app.tasks import worker
    src = inspect.getsource(worker._ingest_document_task_async)
    assert "assess_scan_quality(file_bytes)" in src
    assert 'key="quality_flag"' in src
