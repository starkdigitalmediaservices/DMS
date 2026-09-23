"""Scan quality check run on every uploaded image during ingestion.

Flags blurry, under/over-exposed, blank and low-resolution scans so they
land in "Needs Review" -- the document is always ingested either way.

Lived in scanner_connector.py until that connector was removed (38e8229),
which silently took this check with it even though ingestion called it for
every image regardless of how it arrived. Moved here so it no longer
depends on the scanner feature existing.
"""
import io
import logging
from typing import Any, Dict, List

import numpy as np
from PIL import Image
from scipy import ndimage

from app.config import settings

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
_IMAGE_MAGIC = (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\x89PNG")


def is_image(filename_ext: str, file_bytes: bytes) -> bool:
    return filename_ext in IMAGE_EXTENSIONS or file_bytes[:4] in _IMAGE_MAGIC


def _report(passed, warnings, sharpness, brightness, blank_variance, skew, resolution) -> Dict[str, Any]:
    return {
        "passed": passed,
        "warnings": warnings,
        "sharpness_score": round(sharpness, 2),
        "brightness_score": round(brightness, 2),
        "blank_variance_score": round(blank_variance, 2),
        "skew_angle_degrees": round(skew, 2),
        "skew_confidence": "low",
        "resolution": resolution,
    }


def assess_scan_quality(image_bytes: bytes) -> Dict[str, Any]:
    """Blur, exposure, blank-page and resolution checks (skew is reported
    but informative only). Returns {"passed", "warnings", ...scores}."""
    if not settings.scan_quality_check_enabled:
        return _report(True, [], 999.0, 128.0, 1000.0, 0.0, (1000, 1000))

    warnings: List[str] = []
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
    except Exception as e:
        logger.warning("Scan quality check: unable to parse image bytes: %s", e)
        return _report(True, ["unparseable_image_format"], 0.0, 0.0, 0.0, 0.0, (0, 0))

    if min(width, height) < settings.scan_quality_min_resolution_px:
        warnings.append("low_resolution")

    try:
        gray_arr = np.array(img.convert("L"), dtype=np.float64)
    except Exception as e:
        logger.warning("Scan quality check: failed grayscale conversion: %s", e)
        return _report(True, [], 100.0, 128.0, 1000.0, 0.0, (width, height))

    sharpness = float(ndimage.laplace(gray_arr).var())
    if sharpness < settings.scan_quality_min_sharpness:
        warnings.append("blurry")

    brightness = float(np.mean(gray_arr))
    if brightness < settings.scan_quality_min_brightness:
        warnings.append("underexposed")
    elif brightness > settings.scan_quality_max_brightness:
        warnings.append("overexposed")

    blank_variance = float(np.var(gray_arr))
    if blank_variance < settings.scan_quality_min_blank_variance:
        warnings.append("possible_blank_page")

    skew = 0.0
    try:
        gy, gx = np.gradient(gray_arr)
        skew = float(np.degrees(np.median(np.arctan2(gy, gx)))) % 90
        if skew > 45:
            skew = 90 - skew
    except Exception:
        skew = 0.0

    return _report(not warnings, warnings, sharpness, brightness, blank_variance, skew, (width, height))
