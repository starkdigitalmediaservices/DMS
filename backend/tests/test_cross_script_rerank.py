"""Regression tests for the reranker's cross-script secondary probe.

Guards a real, live-measured defect (2026-09-18): Cohere scores a Devanagari
query at ~0.000 against this corpus' content, so a Marathi search returned
"no matches" for documents the vector stage had already found correctly
(top cosine 0.62, but rerank 0.0005 and 0/19 candidates over the 0.15
threshold; the English form of the same query scored 0.91 and passed 17/17).

The cause was that the rerank stage always used the Marathi expansion as its
second probe, which for a query already typed in Devanagari is the same
script as the query itself -- two near-zero probes, no results. The probe has
to cross scripts. These tests are pure-function and make no API calls.
"""
from app.services.search_service import _pick_secondary_rerank_probe


def _expanded(english="Waqf Property List", marathi="वक्फ मालमत्ता यादी"):
    return {"english": english, "marathi": marathi, "hindi": "वक़्फ़ संपत्ति सूची"}


def test_devanagari_query_is_probed_with_the_english_variant():
    """The actual bug: a Marathi query must not be paired with a Marathi probe."""
    probe = _pick_secondary_rerank_probe("वक्फ मालमत्तेची यादी", _expanded())
    assert probe == "Waqf Property List"


def test_english_query_is_probed_with_the_marathi_variant():
    """Pre-existing cross-script behaviour must survive: an English query still
    reaches Devanagari-OCR'd documents via the Marathi probe."""
    probe = _pick_secondary_rerank_probe("waqf property list", _expanded())
    assert probe == "वक्फ मालमत्ता यादी"


def test_secondary_probe_is_never_the_same_script_as_a_devanagari_query():
    """Script-level assertion, independent of the exact expansion strings --
    this is the property that actually prevents the regression."""
    devanagari = range(0x0900, 0x0980)
    probe = _pick_secondary_rerank_probe("मशिदीची नोंदणी यादी", _expanded())
    assert probe is not None
    assert not any(ord(ch) in devanagari for ch in probe)


def test_mixed_script_query_counts_as_devanagari():
    """A query with any Devanagari in it is one Cohere scores poorly, so it
    gets the English probe -- matching on 'any Devanagari', not 'all'."""
    probe = _pick_secondary_rerank_probe("Aurangabad वक्फ", _expanded())
    assert probe == "Waqf Property List"


def test_probe_identical_to_query_is_skipped():
    """Never spend a second rerank call on the same string -- each probe is a
    separate API call and the Cohere trial key allows only 10/minute."""
    assert _pick_secondary_rerank_probe("waqf property list", _expanded(marathi="waqf property list")) is None
    assert _pick_secondary_rerank_probe("वक्फ मालमत्ता", _expanded(english="वक्फ मालमत्ता")) is None


def test_missing_or_blank_expansion_yields_no_probe():
    """Query expansion is an LLM call that can degrade; a missing variant must
    return None rather than reranking against an empty string."""
    assert _pick_secondary_rerank_probe("वक्फ मालमत्ता", {}) is None
    assert _pick_secondary_rerank_probe("वक्फ मालमत्ता", {"english": "   "}) is None
    assert _pick_secondary_rerank_probe("waqf property", {"marathi": None}) is None
