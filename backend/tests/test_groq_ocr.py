"""Groq vision OCR engine (extractor._groq_ocr_image / GroqOCRProvider).

HTTP is faked: these pin the behaviour that matters on Groq's free tier --
blocked-model keys are skipped, 429s rotate keys and honour retry-after,
oversized pages are downscaled before upload -- without spending quota.
"""
import base64
import io
import json

import pytest
from PIL import Image

from app.config import settings
from app.ocr import extractor


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _ok(text):
    return _Resp(200, {"choices": [{"message": {"content": text}}]})


class _FakeClient:
    def __init__(self, script):
        self.script = script  # key -> list of responses (popped in order)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers, json):
        key = headers["Authorization"].split(" ", 1)[1]
        self.calls.append((key, json))
        return self.script[key].pop(0)


@pytest.fixture
def groq(monkeypatch):
    import httpx
    extractor._groq_blocked_keys.clear()
    monkeypatch.setattr(extractor, "_groq_key_cursor", 0)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)  # _groq_ocr_image imports time locally

    def install(keys, script):
        client = _FakeClient(script)
        monkeypatch.setattr(type(settings), "get_groq_api_keys", lambda self: keys)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        return client
    return install


def _page(w=800, h=600):
    return Image.new("RGB", (w, h), "white")


def test_returns_transcription_and_strips_fences(groq):
    groq(["k1"], {"k1": [_ok("```text\nसर्व्हे नं. 42\nOwner: Ramesh\n```")]})
    assert extractor._groq_ocr_image(_page()) == "सर्व्हे नं. 42\nOwner: Ramesh"


def test_blocked_org_key_is_skipped_and_remembered(groq):
    blocked = _Resp(403, {"error": {"message": "The model is blocked at the organization level"}})
    client = groq(["bad", "good"], {"bad": [blocked], "good": [_ok("one"), _ok("two")]})
    assert extractor._groq_ocr_image(_page()) == "one"
    assert extractor._groq_ocr_image(_page()) == "two"
    assert [k for k, _ in client.calls] == ["bad", "good", "good"]


def test_rate_limit_rotates_to_next_key(groq):
    limited = _Resp(429, {"error": {"message": "rate limit"}}, {"retry-after": "9"})
    client = groq(["a", "b"], {"a": [limited], "b": [_ok("text")]})
    assert extractor._groq_ocr_image(_page()) == "text"
    assert [k for k, _ in client.calls] == ["a", "b"]


def test_all_keys_limited_waits_then_retries(groq):
    limited = lambda: _Resp(429, {"error": {"message": "rate limit"}}, {"retry-after": "1"})
    client = groq(["a"], {"a": [limited(), limited(), _ok("finally")]})
    assert extractor._groq_ocr_image(_page()) == "finally"
    assert len(client.calls) == 3


def test_no_usable_key_raises(groq):
    blocked = _Resp(403, {"error": {"message": "blocked at the organization level"}})
    groq(["only"], {"only": [blocked]})
    with pytest.raises(Exception, match="Groq OCR"):
        extractor._groq_ocr_image(_page())


def test_large_page_is_downscaled_under_the_pixel_cap(groq, monkeypatch):
    monkeypatch.setattr(settings, "groq_ocr_max_pixels", 1_000_000)
    client = groq(["k"], {"k": [_ok("x")]})
    extractor._groq_ocr_image(_page(3000, 4000))
    url = client.calls[0][1]["messages"][0]["content"][1]["image_url"]["url"]
    sent = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert sent.width * sent.height <= 1_000_000
    assert abs(sent.width / sent.height - 0.75) < 0.01


def test_image_file_goes_through_groq_engine(groq):
    groq(["k"], {"k": [_ok("Survey No. 42 land record")]})
    buf = io.BytesIO()
    _page().save(buf, format="PNG")
    pages = extractor.extract_pages_from_file(buf.getvalue(), "scan.png", "groq")
    assert pages[0]["text"] == "Survey No. 42 land record"
    assert pages[0]["extraction_failed"] is False


def test_factory_builds_groq_provider(monkeypatch):
    from app.ocr import factory
    from app.ocr.providers.groq_ocr_provider import GroqOCRProvider
    monkeypatch.setattr(settings, "ai_ocr_provider", "groq")
    monkeypatch.setattr(factory, "_ocr_provider", None)
    assert isinstance(factory.get_ocr_provider(), GroqOCRProvider)
    monkeypatch.setattr(factory, "_ocr_provider", None)
