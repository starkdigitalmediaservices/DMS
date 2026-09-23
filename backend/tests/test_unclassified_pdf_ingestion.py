"""Regression for a NameError introduced by the scanner-feature removal
(caught by another session's pyflakes run on 2026-09-23, after the code had
already been committed).

worker.py decides whether to run VLM extraction with:

    if template or is_scanned_image or ext == ".pdf":

`ext` used to be defined a few lines above, inside the scan-quality block
that the scanner removal deleted. The definition went with the block; the
use did not. Python evaluates `or` left to right with short-circuiting, so
the undefined name is only reached when BOTH earlier operands are falsy --
a PDF that matched no template. Every existing test either supplies a
template or uses an image, so 412 passing tests missed it entirely.

This test asserts the decision expression is evaluable for exactly that
case, without standing up the whole Celery task.
"""
import os

import pytest


def _decide(filename: str, template, *, ext_defined: bool = True):
    """Mirror of worker.py's extraction gate, preserving `or` short-circuiting.

    Written as explicit steps rather than one boolean expression so the
    NameError is raised only when evaluation actually REACHES `ext` -- which
    is the whole point of the bug.
    """
    is_scanned_image = filename.lower().rsplit(".", 1)[-1] in {
        "jpg", "jpeg", "png", "tiff", "bmp", "webp"
    }
    if template:
        return True
    if is_scanned_image:
        return True
    if not ext_defined:
        raise NameError("name 'ext' is not defined")
    ext = os.path.splitext(filename)[1].lower()
    return ext == ".pdf"


def test_unclassified_pdf_still_reaches_extraction():
    """The exact case that raised NameError: PDF, no template."""
    assert _decide("register.pdf", None) is True


def test_the_regression_shape_is_what_we_think_it_is():
    """Guard the guard: with ext undefined, this case is the one that breaks,
    and the short-circuiting cases are the ones that silently did not."""
    with pytest.raises(NameError):
        _decide("register.pdf", None, ext_defined=False)

    # these never reached `ext`, which is why the bug hid
    assert _decide("scan.png", None, ext_defined=False) is True
    assert _decide("register.pdf", object(), ext_defined=False) is True


def test_worker_module_has_no_undefined_names():
    """Belt and braces -- import the real module and pyflakes it."""
    import subprocess
    import sys

    import app.tasks.worker as worker_mod

    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", worker_mod.__file__],
        capture_output=True, text=True,
    )
    undefined = [l for l in result.stdout.splitlines() if "undefined name" in l]
    assert not undefined, f"undefined names in worker.py: {undefined}"
