"""Chandra OCR keeps its json layout tree (extractor._chandra_ocr_image_with_layout).

HTTP is faked with the response shape Datalab actually returned for a live
probe (2026-09-24, output_format="html,json"): text still comes from the
html, and the root Page block of the json tree is persisted on the page dict
under "layout" -- trimmed of base64 images and polygons.
"""
import io
import json

import pytest
from PIL import Image

from app.config import settings
from app.ocr import extractor

_PAGE = {
    "id": "/page/0/Page/0", "block_type": "Page", "bbox": [0.0, 0.0, 3080.0, 1540.0],
    "polygon": [[0, 0], [3080, 0], [3080, 1540], [0, 1540]],
    "html": "<h1>INVOICE HEADER</h1><p>Body text</p>",
    "children": [
        {"id": "/page/0/SectionHeader/0", "block_type": "SectionHeader", "html": "<h1>INVOICE HEADER</h1>",
         "bbox": [80.0, 93.0, 532.0, 160.0], "polygon": [[80, 93]], "images": {}},
        {"id": "/page/0/Picture/1", "block_type": "Picture", "html": "", "bbox": [80.0, 200.0, 400.0, 380.0],
         "images": {"/page/0/Picture/1": "iVBORw0KGgo" * 1000}},
        {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>Body text</p>", "bbox": [80.0, 401.0, 714.0, 475.0]},
    ],
}
_COMPLETE = {
    "status": "complete",
    "html": "<html><body><h1>INVOICE HEADER</h1><p>Body text</p></body></html>",
    "json": {"children": [_PAGE], "metadata": {}},
}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, poll_body):
        self.poll_body = poll_body
        self.submitted = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers, files, data):
        self.submitted = data
        return _Resp(200, {"request_check_url": "https://example.invalid/check"})

    def get(self, url, headers):
        return _Resp(200, self.poll_body)


@pytest.fixture
def chandra(monkeypatch):
    import httpx
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(settings, "datalab_api_key", "test-key")

    def install(poll_body):
        client = _FakeClient(poll_body)
        monkeypatch.setattr(httpx, "Client", lambda timeout: client)
        return client
    return install


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (60, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_layout_tree_is_requested_and_kept(chandra):
    client = chandra(_COMPLETE)
    pages = extractor._extract_image(_png(), "scan.png", "chandra")

    assert client.submitted["output_format"] == "html,json"
    page = pages[0]
    assert page["extraction_failed"] is False
    assert "INVOICE HEADER" in page["text"] and "Body text" in page["text"]

    layout = page["layout"]
    assert layout["engine"] == "chandra"
    root = layout["page"]
    assert root["block_type"] == "Page" and root["bbox"] == [0.0, 0.0, 3080.0, 1540.0]
    assert [c["block_type"] for c in root["children"]] == ["SectionHeader", "Picture", "Text"]
    assert root["children"][0]["bbox"] == [80.0, 93.0, 532.0, 160.0]
    # base64 crops and polygons are not persisted
    assert "images" not in json.dumps(layout) and "polygon" not in json.dumps(layout)


def test_text_unchanged_when_json_missing(chandra):
    chandra({"status": "complete", "html": "<p>Plain paragraph without layout</p>"})
    page = extractor._extract_image(_png(), "scan.png", "chandra")[0]
    assert page["text"] == "Plain paragraph without layout"
    assert "layout" not in page


def test_text_only_wrapper_still_returns_str(chandra):
    chandra(_COMPLETE)
    img = Image.open(io.BytesIO(_png()))
    assert isinstance(extractor._chandra_ocr_image(img), str)
