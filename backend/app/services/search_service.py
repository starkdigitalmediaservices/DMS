import asyncio
import time
import re
import logging
from typing import List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from app.schemas.search import SearchResponse, SearchResult, Citation
from app.services.cache_service import (
    get_cached_search, cache_search_result, generate_cache_key,
    get_cached_expansion, cache_expansion,
    get_cached_embeddings, cache_embeddings,
)
from app.services.audit_service import log_action
from app.services.storage_service import generate_presigned_url
import json
from app.ai.factory import get_embed_provider, get_rerank_provider, get_llm_provider
from app.ai.base import Message, RankedResult
from app.models.metadata_item import MetadataItem
from app.services.config_service import get_int, get_float, get_str
from app.services.search_glossary_service import expand_query_terms
from app.services.duplicate_service import resolve_duplicate_representatives

logger = logging.getLogger(__name__)


def _select_relevant_ranks(reranked_primary: list, relevance_threshold: float, fallback_ratio: float) -> list:
    """Real bug found live (verification report, 2026-09-09): a
    near-identical follow-up query returned "no matching documents" even
    though the same-topic query moments earlier returned 9 results — a
    hard cutoff right at relevance_threshold means ordinary reranker score
    sensitivity to minor wording differences can flip a genuinely relevant
    result from "found" to "nothing" between two questions a person would
    consider the same search.

    Extracted as its own pure function (rather than lowering
    relevance_threshold globally, which would raise false-positive risk
    for every query, not just borderline ones) so the exact boundary
    behavior is unit-testable without depending on a real reranker's
    scores: only the EMPTY case gets a second, narrower look — the single
    best candidate, if it's still within relevance_threshold *
    fallback_ratio, not just any low score. A candidate that misses by a
    wide margin still correctly returns nothing."""
    relevant = sorted(
        (r for r in reranked_primary if r.score >= relevance_threshold),
        key=lambda r: r.score, reverse=True,
    )
    if relevant or not reranked_primary:
        return relevant

    fallback_threshold = relevance_threshold * fallback_ratio
    best = max(reranked_primary, key=lambda r: r.score)
    if best.score >= fallback_threshold:
        logger.info(
            "Relevance fallback: best candidate scored %.3f, below primary threshold %.3f "
            "but within the %.3f fallback margin — returning it instead of 'no matches'.",
            best.score, relevance_threshold, fallback_threshold,
        )
        return [best]
    return []


def _unranked_fallback(merged, doc_texts, limit: int) -> List[RankedResult]:
    """Build the result set used when the reranker is unavailable (rate
    limited, timed out, misconfigured), preserving the RRF fusion order
    the hybrid legs already produced.

    This used to hand back score=0.0 for every row. The ORDER was still
    RRF's, so results were sensibly ranked, but every caller — the API
    response, the UI's relevance display — saw a flat zero and could not
    tell a strong hybrid match from a weak one. RRF scores are tiny by
    construction (sum of 1/(k+rank), k=60), so they're normalised against
    the best candidate in this slice: relative strength survives, and the
    0-1 shape stays comparable to a reranker score. The response's
    `reranked: false` flag is what tells a consumer these came from fusion
    rather than the cross-encoder — the score itself is no longer a lie.
    """
    candidates = merged[: limit * 2]
    if not candidates:
        return []
    top_score = candidates[0][1] or 1.0
    return [
        RankedResult(index=i, score=(rrf_score / top_score), text=doc_texts[i])
        for i, (_cid, rrf_score) in enumerate(candidates)
        if i < len(doc_texts)
    ]


def _make_snippet(content: str, max_chars: int = 400) -> str:
    """Truncate result snippet around boundary to keep payload light."""
    if not content or len(content) <= max_chars:
        return content or ""
    return content[:max_chars].rsplit(" ", 1)[0] + "…"


_FACT_ID_TOKEN_RE = re.compile(r"\b[A-Za-z]{1,6}-\d{1,6}\b")


def _extract_fact_id_tokens(query: str) -> list[str]:
    """A natural-language question ('what is the value of movable property
    for WB-100...') never contains a fact's field_name/value as a literal
    full-string substring, so the structured-fact search leg's plain
    %query% ILIKE (below) only ever fired for a short keyword search, not
    a real question -- silently starving the grounded-answer LLM of the
    one source (structured Facts, with real field_name labels) that could
    have kept it from mixing up which number belongs to which column when
    it fell back to jumbled raw OCR table text instead (real bug: asking
    for WB-100's movable property returned its gross_income figure,
    because both numbers sit in the same undifferentiated table-row chunk
    text). ID-shaped tokens (WB-100, CTS-452, ...) ARE carried verbatim
    in a natural-language question even when the rest of the sentence
    isn't a literal match for anything."""
    return list(dict.fromkeys(_FACT_ID_TOKEN_RE.findall(query)))


async def _find_pending_title_matches(
    db: AsyncSession, tenant_id: UUID, query: str, exclude_doc_ids: set
) -> List[SearchResult]:
    """T74 — a document must be findable by metadata before indexing finishes.

    Content search filters on status='indexed', so a still-processing
    document is otherwise invisible until Celery catches up. This matches
    by title alone (no chunks exist yet for a pending document) and marks
    the result as pending so the caller can show it's still being indexed.
    """
    stmt = text("""
        SELECT d.id, d.title, d.status, v.s3_path
        FROM doc_dg_documents d
        LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id
        WHERE d.tenant_id = CAST(:tenant_id AS uuid)
          AND d.is_trashed = false
          AND d.status IN ('pending', 'processing')
          AND d.title ILIKE :title_pattern
        LIMIT 5
    """)
    res = await db.execute(stmt, {"tenant_id": str(tenant_id), "title_pattern": f"%{query}%"})
    rows = res.fetchall()

    matches = []
    for row in rows:
        if row.id in exclude_doc_ids:
            continue
        url = await generate_presigned_url(row.s3_path) if row.s3_path else ""
        matches.append(SearchResult(
            document_id=row.id,
            document_name=row.title,
            download_url=url,
            page_number=None,
            snippet="This document is still being processed (OCR, chunking, embedding) — full-text search will include it shortly.",
            score=0.0,
            metadata={"pending": True, "status": row.status},
        ))
    return matches


_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _numbers_in_text(text_: str) -> set:
    """Digit sequences with commas/trailing punctuation stripped, so
    'Rs.30,119/-' and '30119' both normalise to '30119' -- a cheap,
    currency/format-agnostic way to check a numeric claim actually
    appears in the excerpt(s) it cites, rather than trusting the model's
    self-reported citation at face value (real bug: it cited a real
    excerpt containing several distinct figures, but stated a number from
    the WRONG column as the answer -- a valid excerpt index alone doesn't
    mean the specific number claimed is actually the one that excerpt
    supports)."""
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text_) if n.replace(",", "").rstrip(".")}


def _parse_claims_json(raw: str):
    """Best-effort parse of the LLM's structured claim response — tolerates
    ```json fences the model adds despite being told not to."""
    raw = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _condense_excerpt_text(content: str) -> str:
    """Found live 2026-09-04: a sparse form layout's OCR text (see
    collapse_blank_lines) handed VERBATIM to the grounded-answer LLM (T70)
    reads as much weaker evidence than the same label/value adjacent —
    the model refused to answer ("answerable": false) even though the
    correct value was right there in the excerpt, walled off by blank
    lines. Collapsing them is what let the same excerpt get answered
    correctly in a live re-test. See also collapse_blank_lines's own
    docstring and chunker.py, which now applies the same collapsing
    before chunk boundaries are decided, not just here at display time."""
    from app.utils.text import collapse_blank_lines
    return collapse_blank_lines(content)


async def _generate_grounded_answer(query: str, response_lang: str, excerpts: list):
    """T70 — bind every claim in the answer to the excerpt(s) that actually
    support it, and refuse outright when the excerpts don't answer the
    question. This is the product's hard rule (Section 8): a confident,
    well-sourced-*looking* answer with nothing behind it is the exact
    failure this gate exists to prevent — so an unsupported claim is
    dropped, not shown, and an unanswerable question gets no answer at all
    rather than a plausible-sounding guess.

    `response_lang` is the language the user wants the ANSWER in, which
    is not always the language the query was typed in -- see
    _expand_trilingual_query's response_lang field (live bug, 2026-09-04:
    a query like "Explain this document in Marathi" is itself written in
    English, and the caller used to pass that raw detected/written
    language straight through here, so an explicit request for a Marathi
    answer got silently answered in English).

    Returns (summary_text_or_None, citations, grounded).
    """
    llm = get_llm_provider()

    numbered = "\n\n".join(
        f"[{i + 1}] Document: {e['document_name']} (Page {e['page_number'] or 1})\n{_condense_excerpt_text(e['content'])}"
        for i, e in enumerate(excerpts)
    )

    sys_msg = (
        "You are an enterprise multilingual document intelligence assistant. "
        "Answer strictly and only from the numbered excerpts below — never from outside knowledge.\n"
        f"Respond in {response_lang} — this is the language the user wants the answer in, which may differ from "
        "whatever language the excerpts themselves are written in (translate the substance, keep proper nouns as-is).\n\n"
        "The user's query may be a natural-language question (\"what is the salary of X\") OR a short "
        "keyword/name/phrase search (\"Aurangabad-Shia\", a document title, a place or person name). For a "
        "keyword/phrase query, treat it as \"summarize what these excerpts say that is relevant to this topic\" "
        "rather than requiring a literal question to be answered.\n\n"
        'Respond with ONLY a single JSON object, no markdown fences, no other text, in this exact shape:\n'
        '{"answerable": true, "claims": [{"text": "one factual statement", "sources": [1, 2]}]}\n\n'
        "Rules:\n"
        "- Break your answer into individual factual claims. Each claim's \"sources\" must list every excerpt "
        "number that actually supports it. Never cite an excerpt that does not contain the stated fact.\n"
        '- Respond with {"answerable": false, "claims": []} ONLY if the excerpts are unrelated to the query '
        "topic — not merely because the query isn't phrased as a question. Do not answer from outside knowledge "
        "and do not guess — this is a hard rule, not a style preference.\n"
        "- Never compute a sum, count, average, or other aggregate across multiple excerpts or entries — state "
        "only figures that appear verbatim in a single excerpt. If the query asks for a total/count/average "
        "across many entries and no excerpt already states that aggregate directly, treat it as unanswerable.\n"
        "- A row from a table can list several distinct numbers (value, income, tax, ...). Double-check which "
        "specific number the query is actually asking for before stating it — do not answer with a different "
        "field's figure from the same row.\n"
        "- Never name or assume which specific document a claim comes from in your claim text (e.g. do not write "
        "\"According to X.pdf...\") — the numbered citation markers are the only authoritative source identity, "
        "and a claim's own \"sources\" list is what actually determines which document(s) it displays as coming "
        "from. Real bug found live: the corpus can contain several near-duplicate scans of the same underlying "
        "document, and a query that names one specific document by title can still retrieve a chunk from a "
        "different, near-identical scan — if your claim text separately asserts a document name (e.g. echoing "
        "the one the user's own question mentioned) while its citation marker points at a different excerpt's "
        "document, the two disagree and the answer reads as self-contradictory even though each half is doing "
        "its own job correctly. Describe what the excerpts say; let the citation numbers carry the source.\n"
        "- Keep claim text natural and complete; bold key numbers/names/dates with **markdown** where useful."
    )
    user_msg = f"User query: {query}\n\nNumbered excerpts:\n{numbered}"

    raw = await llm.complete([
        Message(role="system", content=sys_msg),
        Message(role="user", content=user_msg),
    ], max_tokens=2500)

    parsed = _parse_claims_json(raw)
    if not parsed or not parsed.get("answerable") or not parsed.get("claims"):
        return None, [], False

    summary_lines = []
    citations = []
    # T71: number citations by unique (document, page) — not by excerpt index —
    # so two excerpts from the same page share one marker instead of two.
    source_number_by_key: dict = {}

    for claim in parsed["claims"]:
        claim_text = str(claim.get("text", "")).strip()
        sources = claim.get("sources", [])
        if not claim_text or not isinstance(sources, list):
            continue
        valid_sources = [s for s in sources if isinstance(s, int) and 1 <= s <= len(excerpts)]
        if not valid_sources:
            # The model cited nothing real for this claim — drop the claim
            # rather than show an unbound statement.
            continue

        # Grounding check: every number the claim states must actually
        # appear in the excerpt text it cites. A valid excerpt index alone
        # doesn't guarantee the specific figure claimed is the one that
        # excerpt supports — a dense table-row excerpt can carry several
        # distinct numbers, and the model can grab the wrong one (real bug:
        # asked for a movable-property value, answered with that same
        # row's gross income instead). Also catches outright invention,
        # like a computed total that appears nowhere in any excerpt.
        claim_numbers = _numbers_in_text(claim_text)
        if claim_numbers:
            cited_text = " ".join(_condense_excerpt_text(excerpts[s - 1]["content"]) for s in valid_sources)
            if not claim_numbers.issubset(_numbers_in_text(cited_text)):
                continue

        cited_markers = []
        for s in valid_sources:
            ex = excerpts[s - 1]
            key = (ex["document_id"], ex["page_number"])
            if key not in source_number_by_key:
                source_number_by_key[key] = len(source_number_by_key) + 1
            n = source_number_by_key[key]
            if n not in cited_markers:
                cited_markers.append(n)
            citations.append(Citation(
                number=n,
                claim=claim_text,
                document_id=ex["document_id"],
                document_name=ex["document_name"],
                page_number=ex["page_number"],
                chunk_id=ex.get("chunk_id"),
                fact_id=ex.get("fact_id"),
            ))

        markers = "".join(f" [{n}]" for n in sorted(cited_markers))
        summary_lines.append(f"- {claim_text}{markers}")

    if not summary_lines:
        return None, [], False

    return "\n".join(summary_lines), citations, True


async def _expand_trilingual_query(query: str, _attempts: int = 2) -> dict:
    """Expand user query into normalized English, Hindi, and Marathi search variants.

    Retries the LLM call/parse a couple of times before giving up: the
    provider already rotates across API keys for network/rate-limit
    failures (see GroqLLMProvider), but a malformed-JSON response or a
    borderline detected_lang/response_lang call isn't a network error, so
    it was hitting the static English/English fallback on the very first
    bad draw -- silently downgrading multilingual query understanding for
    the whole request on a single flaky sample. One retry gets a second,
    independent sample from the model before giving up.

    Cached in Redis: this round-trip measured ~1.2s, about 22% of an
    uncached search, and the expansion depends only on the query text --
    no tenant data, no filters -- so the second person to ask a given
    question can skip it entirely. Only SUCCESSFUL expansions are cached;
    see the fallback at the end of this function for why."""
    cached = await get_cached_expansion(query)
    if cached:
        return cached

    llm = get_llm_provider()
    sys_msg = (
        "You are a multilingual AI query normalization assistant for enterprise document search in India.\n"
        "Analyze the user query (which could be in English, Hindi, Marathi, or Hinglish) and output a JSON object with 5 keys:\n"
        '- "detected_lang": the language the QUERY TEXT ITSELF is written in (e.g. "English", "Hindi", "Marathi", "Hinglish")\n'
        '- "response_lang": the language the user wants the ANSWER written in. Almost always the same as '
        'detected_lang -- EXCEPT when the query explicitly asks for a different output language regardless of '
        'what language the query itself is written in (e.g. the English sentence "Explain this document in '
        'Marathi" has detected_lang "English" but response_lang "Marathi"; "मराठीत सांग" has detected_lang '
        '"Marathi" and response_lang "Marathi" too, since there is no separate request there). Never invent a '
        "requested language that isn't actually named in the query -- only differs from detected_lang when the "
        "query explicitly names a target language.\n"
        '- "english": concise normalized search query in English stripping away conversational filler words (e.g. "kunal deshmukh che aadhar card ahe ka aaplya files madhe?" -> "Kunal Deshmukh Aadhar Card")\n'
        '- "hindi": concise search keywords in Hindi (Devanagari script)\n'
        '- "marathi": concise search keywords in Marathi (Devanagari script)\n'
        "Output ONLY valid JSON. No markdown formatting."
    )

    last_error: Exception | None = None
    for attempt in range(_attempts):
        try:
            resp = await llm.complete([
                Message(role="system", content=sys_msg),
                Message(role="user", content=f"Query: {query};")
            ])
            clean_json = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(clean_json)
            logger.info("Tri-lingual query expansion for '%s': %s", query, data)
            detected_lang = data.get("detected_lang", "English")
            expansion = {
                "detected_lang": detected_lang,
                "response_lang": data.get("response_lang") or detected_lang,
                "english": data.get("english", query),
                "hindi": data.get("hindi", query),
                "marathi": data.get("marathi", query),
            }
            await cache_expansion(query, expansion)
            return expansion
        except Exception as e:
            last_error = e
            logger.warning("Tri-lingual query expansion attempt %d/%d failed: %s", attempt + 1, _attempts, e)
            if attempt + 1 < _attempts:
                # A back-to-back retry with no delay lands in the same
                # exhausted-quota window as the first call under sustained
                # load (confirmed live, 2026-09-15: Groq returned an empty
                # body across all rotated keys for several seconds straight
                # during the backend test suite's own real-API traffic) --
                # a short backoff gives a brief provider-side blip a chance
                # to actually clear before the next attempt.
                await asyncio.sleep(1.5)

    # Deliberately NOT cached. This is the call-failed fallback (query
    # unchanged in all three languages); caching it would pin a degraded
    # monolingual expansion for 24h on the strength of one bad LLM call,
    # and every later search for that query would silently lose
    # cross-script matching -- which on this Marathi corpus is what finds
    # results at all (see _pick_secondary_rerank_probe).
    logger.warning("Tri-lingual query expansion failed after %d attempt(s): %s", _attempts, last_error)
    return {"detected_lang": "English", "response_lang": "English", "english": query, "hindi": query, "marathi": query}


_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _pick_secondary_rerank_probe(query: str, expanded: dict) -> str | None:
    """Pick the cross-script counterpart of the user's query to use as the
    reranker's second probe.

    Measured live 2026-09-18 against this corpus: Cohere scores a
    Devanagari query at ~0.000 against the very chunks that answer it
    ('वक्फ मालमत्तेची यादी' -> top rerank score 0.0005, 0/19 candidates
    cleared the 0.15 threshold), while the English form of that same
    query scored 0.91 and passed 17/17 on the same candidate set. Vector
    retrieval is fine either way (top cosine 0.62 vs 0.61) -- it is only
    the rerank stage that collapses, so a Devanagari query used to return
    "no matches" for content the system had already found.

    The old rule always paired the query with the Marathi variant, which
    for a Devanagari query is the same script as the query itself: two
    near-zero probes and no results. Pairing across scripts fixes that.
    Deliberately ONE secondary probe, not both variants: each probe is a
    separate rerank call and the Cohere trial key allows 10/minute.
    """
    q_en = (expanded.get("english") or "").strip()
    q_mr = (expanded.get("marathi") or "").strip()
    secondary = q_en if _DEVANAGARI_RE.search(query) else q_mr
    return secondary if secondary and secondary != query else None


async def _generate_trilingual_hyde(query: str, expanded: dict, _attempts: int = 2) -> list[str]:
    """Generate realistic hypothetical document excerpts (HyDE) in English, Hindi, and Marathi.

    Retried like _expand_trilingual_query, and for the same reason: this is
    the last retrieval leg before a search gives up, so a single flaky LLM
    draw here is the difference between real results and a "no matches"
    answer. Confirmed live 2026-09-18 -- the identical Devanagari query
    returned 2 results at 08:56 and 0 at 09:03, the failing run logging
    hyde_success=false after one bad sample.
    """
    llm = get_llm_provider()
    sys_msg = (
        "You are an AI document intelligence system. Generate realistic, formal 1-sentence hypothetical document excerpts "
        "or record lines that directly answer the query in 3 languages:\n"
        "1. English excerpt\n"
        "2. Hindi excerpt (Devanagari script)\n"
        "3. Marathi excerpt (Devanagari script)\n"
        'Output a JSON list of 3 strings: ["english excerpt", "hindi excerpt", "marathi excerpt"]. Output ONLY valid JSON.'
    )

    last_error: Exception | None = None
    for attempt in range(_attempts):
        try:
            resp = await llm.complete([
                Message(role="system", content=sys_msg),
                Message(role="user", content=f"Query: {query}\nEnglish Context: {expanded.get('english')}")
            ])
            clean_json = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            snippets = json.loads(clean_json)
            if isinstance(snippets, list) and len(snippets) > 0:
                valid_snippets = [s.strip() for s in snippets if isinstance(s, str) and s.strip()]
                if valid_snippets:
                    logger.info("Generated Tri-Lingual HyDE snippets for '%s': %s", query, valid_snippets)
                    return valid_snippets
            raise ValueError(f"HyDE response parsed but yielded no usable snippets: {clean_json[:200]}")
        except Exception as e:
            last_error = e
            logger.warning("Tri-lingual HyDE generation attempt %d/%d failed: %s", attempt + 1, _attempts, e)
            if attempt + 1 < _attempts:
                await asyncio.sleep(1.5)

    # Degraded fallback: embed the expansion variants we already have rather
    # than the English one alone. On a Devanagari query the English variant
    # is the single worst probe to fall back to -- the Hindi/Marathi variants
    # are what actually match this corpus's Devanagari OCR text.
    logger.warning("Tri-lingual HyDE generation failed after %d attempt(s): %s", _attempts, last_error)
    fallback = [expanded.get("english"), expanded.get("hindi"), expanded.get("marathi"), query]
    deduped: list[str] = []
    for s in fallback:
        if isinstance(s, str) and s.strip() and s.strip() not in deduped:
            deduped.append(s.strip())
    return deduped


async def search(
    query: str,
    tenant_id: UUID,
    user_id: UUID,
    limit: int,
    filters: dict | None,
    db: AsyncSession,
    ip_address: str,
    rerank_provider: str | None = None,
    generate_summary: bool = True,
    on_results=None,
) -> SearchResponse:
    """on_results: optional async callback invoked with the assembled
    SearchResult list as soon as retrieval and reranking finish, BEFORE the
    grounded-answer LLM call. Measured warm, results are ready at ~1.8s while
    the summary lands at ~5s, so a caller that can render progressively (see
    the /search/stream endpoint) shows documents ~3s earlier. Nothing about
    the returned SearchResponse changes; a caller that passes no callback
    behaves exactly as before."""
    start_time = time.time()

    # Phase stopwatch. Uncached searches were measured at 7-41s on a warm,
    # idle box while every individual component profiled fast in isolation
    # (embed 0.22s warm, rerank ~0.5s, LLM ~0.9s) — meaning the cost is
    # spread across the pipeline rather than sitting in one call, and there
    # was no way to tell which phase owned it from the outside because only
    # a single total took_ms was ever recorded. These marks make the
    # breakdown visible in the logs so a slow search can be diagnosed from
    # production evidence instead of re-derived by hand each time.
    _phase_marks: list[tuple[str, float]] = []
    _phase_last = [start_time]

    def _mark(label: str) -> None:
        now = time.time()
        _phase_marks.append((label, now - _phase_last[0]))
        _phase_last[0] = now

    search_mode = "direct"
    hyde_triggered = False
    hyde_success = False
    hypothetical_snippet = None
    reranked = True
    grounded = True
    
    # 1. Enforce AI Input Guardrails
    from app.services.guardrail_service import validate_input_query
    is_safe, error_msg, scrubbed_query = validate_input_query(query)
    if not is_safe:
        took_ms = int((time.time() - start_time) * 1000)
        return SearchResponse(
            query=query,
            ai_summary=f"Safety Block: {error_msg}",
            results=[],
            cached=False,
            took_ms=took_ms,
            search_mode="failed_all",
            hyde_triggered=False,
            reranked=True
        )

    query = scrubbed_query
    
    cache_key = generate_cache_key(str(tenant_id), query, filters)
    cached = await get_cached_search(cache_key)
    if cached:
        return cached
        
    # 2. Tri-Lingual Query Expansion (English, Hindi, Marathi) — an external
    # LLM round-trip, run concurrently with the glossary lookup below (TS7)
    # since neither depends on the other's output (real latency fix,
    # 2026-09-10: QA report #6 measured ~7s on an uncached query; this and
    # the two rerank/title-match pairs below were the only genuinely
    # independent steps in an otherwise sequentially-dependent pipeline —
    # expand must finish before embed, embed before search, search before
    # rerank, rerank before the grounded answer, so those hops can't be
    # parallelized away without changing the RAG design itself).
    expand_task = asyncio.create_task(_expand_trilingual_query(query))
    # TS7 — glossary-first cross-script expansion: free, local, always
    # available (unlike the LLM expansion above, which silently degrades
    # to the raw query on any failure, including air-gapped mode). Only
    # ever adds vector-search variants (see search_glossary_service.py's
    # docstring for why this doesn't touch the keyword-search legs).
    glossary_terms = await expand_query_terms(db, query)
    expanded = await expand_task
    detected_lang = expanded.get("detected_lang", "English")
    response_lang = expanded.get("response_lang") or detected_lang
    q_en = expanded.get("english", query)
    q_hi = expanded.get("hindi", query)
    q_mr = expanded.get("marathi", query)

    _mark("expand+glossary")

    embed_provider = get_embed_provider()
    tri_queries = list(dict.fromkeys([q_en, q_hi, q_mr, query, *glossary_terms]))

    # Embed only the variants not already cached. Same reasoning as the
    # expansion cache: a vector is a pure function of its text, so it is
    # reusable across tenants and filters. Order matters to every caller
    # below (q_embeddings is indexed positionally against tri_queries), so
    # the cached and freshly-embedded vectors are recombined in the
    # original variant order rather than appended.
    cached_vectors = await get_cached_embeddings(tri_queries)
    to_embed = [t for t in tri_queries if t not in cached_vectors]
    if to_embed:
        fresh = await embed_provider.embed(to_embed)
        newly = dict(zip(to_embed, fresh))
        await cache_embeddings(newly)
        cached_vectors.update(newly)
    q_embeddings = [cached_vectors[t] for t in tri_queries]
    _mark(f"embed(x{len(tri_queries)},miss={len(to_embed)})")

    # Build filter clauses dynamically for hybrid search
    filter_clauses = []
    params = {
        "query_en": q_en,
        "query_mr": q_mr,
        "tenant_id": str(tenant_id),
    }
    
    if filters:
        for idx, (k, v) in enumerate(filters.items()):
            if k == "doc_type":
                filter_clauses.append(f"AND d.doc_type = :filter_{idx}")
                params[f"filter_{idx}"] = str(v)
            elif k == "document_id":
                filter_clauses.append(f"AND d.id = :filter_{idx}")
                params[f"filter_{idx}"] = str(v)
            else:
                # T73 — a filter key can match either the generic LLM
                # metadata pass (title/author/date/type/topics/summary) or
                # a template-extracted structured field (area, village,
                # status...) on doc_dg_facts. Either source satisfying it
                # is enough — a document doesn't need both.
                filter_clauses.append(f"""
                    AND (
                      EXISTS (
                        SELECT 1 FROM doc_dg_metadata_items m
                        WHERE m.document_id = d.id
                          AND m.key = :filter_key_{idx}
                          AND (
                            m.value->>'v' = :filter_val_{idx}
                            OR m.value->> :filter_key_{idx} = :filter_val_{idx}
                            OR m.value::text = :filter_val_{idx}
                            OR m.value::text LIKE :filter_like_val_{idx}
                          )
                      )
                      OR EXISTS (
                        SELECT 1 FROM doc_dg_facts f2
                        WHERE f2.document_id = d.id
                          AND f2.field_name = :filter_key_{idx}
                          AND COALESCE(f2.value->>'v', f2.value::text) = :filter_val_{idx}
                      )
                    )
                """)
                params[f"filter_key_{idx}"] = str(k)
                params[f"filter_val_{idx}"] = str(v)
                params[f"filter_like_val_{idx}"] = f'%"{v}"%'
                
    filter_str = "\n".join(filter_clauses)
    candidate_limit = await get_int("search_candidate_limit", 20)

    # 3. Vector search (pgvector <=> operator across English, Hindi, Marathi embeddings)
    vec_sql = text(f"""
        SELECT c.id, c.content, c.page_number, c.chunk_index, d.title, d.id as doc_id, v.s3_path,
               1 - (c.embedding <=> CAST(:query_embedding AS vector)) as vector_score
        FROM doc_dg_chunks c
        JOIN doc_dg_documents d ON c.document_id = d.id
        LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id
        WHERE d.tenant_id = CAST(:tenant_id AS uuid) AND d.status = 'indexed' AND d.is_trashed = false {filter_str}
        ORDER BY c.embedding <=> CAST(:query_embedding AS vector)
        LIMIT {candidate_limit}
    """)

    all_vec_rows = []
    for q_emb in q_embeddings:
        q_emb_str = "[" + ",".join(str(f) for f in q_emb) + "]"
        vec_res = await db.execute(vec_sql, {**params, "query_embedding": q_emb_str})
        all_vec_rows.extend(vec_res.fetchall())

    # 4. Keyword search — English (stemmed) and Devanagari/Marathi (unstemmed
    # 'simple' config) are searched against their own matching tsvector column
    # (T75): a 'simple' query against an 'english'-stemmed vector silently
    # drops matches, since english config removes stopwords and stems words
    # the simple-config query never touched.
    kw_sql = text(f"""
        SELECT c.id, c.content, c.page_number, c.chunk_index, d.title, d.id as doc_id, v.s3_path,
               GREATEST(ts_rank(c.content_tsv, q_en), ts_rank(c.content_tsv_simple, q_simple)) as keyword_score
        FROM doc_dg_chunks c
        JOIN doc_dg_documents d ON c.document_id = d.id
        LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id,
        COALESCE(
          NULLIF(plainto_tsquery('english', :query_en), ''),
          NULLIF(websearch_to_tsquery('english', :query_en), ''),
          ''::tsquery
        ) q_en,
        COALESCE(
          NULLIF(plainto_tsquery('simple', :query_mr), ''),
          NULLIF(plainto_tsquery('simple', :query_en), ''),
          ''::tsquery
        ) q_simple
        WHERE (c.content_tsv @@ q_en OR c.content_tsv_simple @@ q_simple)
          AND d.tenant_id = CAST(:tenant_id AS uuid) AND d.status = 'indexed' AND d.is_trashed = false {filter_str}
        ORDER BY keyword_score DESC
        LIMIT {candidate_limit}
    """)
    
    kw_res = await db.execute(kw_sql, params)
    kw_rows = kw_res.fetchall()

    # 4b. Fuzzy/trigram search (T72) — catches misspellings vector search's
    # semantics and keyword search's exact tokens both miss (a typo'd proper
    # noun like "Depshmukh" for "Deshmukh"). word_similarity(), not plain
    # similarity(): matching a short query against a whole chunk of running
    # text with similarity() dilutes the score against chunk length (a real
    # substring match scored ~0.2); word_similarity() finds the best-matching
    # substring instead and scored the same case at 1.0. The threshold is a
    # GUC, not a bind param — SET LOCAL only accepts literals, but this value
    # comes from sys_dg_config, never from the request, so interpolating it
    # is safe. LOCAL keeps it scoped to this transaction, not the pooled
    # connection.
    trgm_threshold = await get_float("search_trigram_threshold", 0.3)
    await db.execute(text(f"SET LOCAL pg_trgm.word_similarity_threshold = {trgm_threshold}"))
    trgm_sql = text(f"""
        SELECT c.id, c.content, c.page_number, c.chunk_index, d.title, d.id as doc_id, v.s3_path,
               word_similarity(:query_en, c.content) as trigram_score
        FROM doc_dg_chunks c
        JOIN doc_dg_documents d ON c.document_id = d.id
        LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id
        WHERE d.tenant_id = CAST(:tenant_id AS uuid) AND d.status = 'indexed' AND d.is_trashed = false {filter_str}
          AND :query_en <% c.content
        ORDER BY trigram_score DESC
        LIMIT {candidate_limit}
    """)
    trgm_res = await db.execute(trgm_sql, params)
    trgm_rows = trgm_res.fetchall()

    # 4c. Structured-record search (T73) — extracted Fact fields, not only
    # chunk text. A user might ask about a value that only exists as an
    # extracted field (owner_name, valuation, survey_no...) and never
    # verbatim as running chunk text the way OCR read the page. Marginalia
    # (field_name="_marginalia", T30) is deliberately excluded — those are
    # free-floating adjudication notes, not extracted record fields.
    # Shaped identically to the chunk legs above (same column names) so it
    # drops into the exact same RRF/rerank/results pipeline unchanged;
    # fact_row_ids (below) is how downstream code tells a fact row from a
    # chunk row apart, the same way vec_cids/kw_cids/trgm_cids already
    # track each leg's origin for search_mode.
    # The raw, un-expanded query — not q_en. Trilingual expansion is a
    # semantic reformulation (T73's own test caught this: it turned
    # "42/1B-Kolhapur" into "42/1B Kolhapur", hyphen to space), which is
    # fine for vector/keyword search but wrong here — a structured field
    # like a survey number or an ID is exactly the kind of literal text a
    # paraphrase shouldn't be allowed to alter before matching it.
    fact_pattern = f"%{query}%"
    fact_id_tokens = _extract_fact_id_tokens(query)
    fact_params = {**params, "fact_pattern": fact_pattern}

    id_clauses = []
    for i, tok in enumerate(fact_id_tokens):
        id_clauses.append(f"COALESCE(f.value->>'v', f.value::text) ILIKE :fact_id_{i}")
        fact_params[f"fact_id_{i}"] = f"%{tok}%"

    # Every clause that can directly match ONE fact by itself -- listed once
    # so the row-group-sibling pull below can reuse the identical condition
    # against f2 instead of drifting out of sync with fact_match_clause.
    direct_clauses = [
        "f.field_name ILIKE :fact_pattern",
        "COALESCE(f.value->>'v', f.value::text) ILIKE :fact_pattern",
        *id_clauses,
    ]
    direct_match_sql = " OR ".join(direct_clauses)

    # T28 -- a direct hit only ever matches the ONE fact whose own value
    # happens to contain it (e.g. the wakf_name field's "Chilla Madar Saheb
    # Naigalli") -- not the sibling fields (valuation, gross_income, ...)
    # from the same table row the user actually asked about. Pull in every
    # fact sharing that row's row_group_id too, so the LLM sees the whole
    # entry as separate, cleanly field-labeled excerpts instead of falling
    # back to a raw OCR chunk that can itself omit the field (cut at a
    # chunk boundary) even though the fact exists cleanly two rows away.
    #
    # Real bug found live 2026-09-09: this sibling pull used to fire ONLY
    # when the query contained an ID-shaped token ("WB-100") -- a plain
    # keyword/name search (e.g. searching just "Chilla Madar Saheb
    # Naigalli", the field's own value, with no ID token in it) matched
    # that ONE field via the plain fact_pattern clause below but never
    # pulled in its row's other fields. Reproduced against a real chat
    # groundedness failure: asked about that entry's valuation, got "no
    # valuation figure is shown" back, because the grounding LLM was never
    # shown the sibling valuation Fact at all -- only the wakf_name one
    # that matched, plus whatever raw chunk text vector/keyword search
    # separately turned up. Gating this on ID-shaped tokens specifically
    # was too narrow a condition for what the sibling pull actually exists
    # to fix: any direct fact match, not just ones shaped like an ID.
    row_group_sibling_clause = f"""
        OR f.row_group_id IN (
            SELECT f2.row_group_id FROM doc_dg_facts f2
            WHERE f2.tenant_id = CAST(:tenant_id AS uuid)
              AND f2.row_group_id IS NOT NULL
              AND ({direct_match_sql.replace("f.value", "f2.value").replace("f.field_name", "f2.field_name")})
        )
    """
    fact_match_clause = f"({direct_match_sql} {row_group_sibling_clause})"

    fact_sql = text(f"""
        SELECT f.id, (f.field_name || ': ' || COALESCE(f.value->>'v', f.value::text)) as content,
               fact_page.page_number as page_number, 0 as chunk_index,
               d.title, d.id as doc_id, v.s3_path
        FROM doc_dg_facts f
        JOIN doc_dg_documents d ON f.document_id = d.id
        LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id
        LEFT JOIN LATERAL (
            SELECT p.page_number FROM doc_dg_fact_regions fr
            JOIN doc_dg_pages p ON p.id = fr.page_id
            WHERE fr.fact_id = f.id
            ORDER BY p.page_number ASC LIMIT 1
        ) fact_page ON true
        WHERE f.tenant_id = CAST(:tenant_id AS uuid) AND d.status = 'indexed' AND d.is_trashed = false
          AND f.field_name != '_marginalia'
          AND {fact_match_clause}
          {filter_str}
        ORDER BY f.confidence DESC NULLS LAST
        LIMIT {candidate_limit * 4}
    """)
    fact_res = await db.execute(fact_sql, fact_params)
    fact_rows = fact_res.fetchall()
    fact_row_ids = {str(r.id) for r in fact_rows}

    # 5. RRF Merge (Reciprocal Rank Fusion across all language vectors, keywords, fuzzy, and structured matches)
    rrf_scores = {}
    docs_map = {}
    k = await get_int("search_rrf_k", 60)

    for rank, row in enumerate(all_vec_rows):
        cid = str(row.id)
        docs_map[cid] = row
        rrf_scores[cid] = rrf_scores.get(cid, 0) + (1.0 / (k + (rank % candidate_limit) + 1))

    for rank, row in enumerate(kw_rows):
        cid = str(row.id)
        docs_map[cid] = row
        rrf_scores[cid] = rrf_scores.get(cid, 0) + (1.0 / (k + rank + 1))

    for rank, row in enumerate(trgm_rows):
        cid = str(row.id)
        docs_map[cid] = row
        rrf_scores[cid] = rrf_scores.get(cid, 0) + (1.0 / (k + rank + 1))

    for rank, row in enumerate(fact_rows):
        cid = str(row.id)
        docs_map[cid] = row
        rrf_scores[cid] = rrf_scores.get(cid, 0) + (1.0 / (k + rank + 1))

    _mark("retrieval-legs(db)")

    merged = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:20]
    relevant_ranks = []
    if merged:
        reranker = get_rerank_provider(override=rerank_provider)
        doc_texts = [docs_map[cid].content for cid, _ in merged]

        try:
            # Real bug found live 2026-09-03: rerank(..., top_n=limit*2) only
            # returns each call's OWN top slice. A document scoring near-zero
            # under the raw query but highly relevant under its translation
            # (exactly the cross-script case this second pass exists for)
            # isn't IN reranked_primary at all, so "if r_trans.index in
            # rank_map" silently dropped it instead of adding it — verified
            # live: a Marathi document scored 0.55 under the translated
            # rerank (well above the 0.15 threshold) but was discarded here
            # every time, making cross-script search fail on every query
            # whose translation-relevant document wasn't already primary-
            # relevant. top_n=len(doc_texts) scores every candidate under
            # both queries, and the merge now adds a translated-only hit
            # instead of requiring it to already exist.
            #
            # Real latency fix, 2026-09-10 (QA report #6): these two Cohere
            # calls score the exact same candidate set under two different
            # query strings and are otherwise independent — firing them
            # concurrently instead of back-to-back was one of the few actual
            # parallelization opportunities in this pipeline (see the
            # asyncio.create_task comment at the top of search() for why
            # most of the rest is genuinely sequential).
            secondary_probe = _pick_secondary_rerank_probe(query, expanded)
            if secondary_probe:
                primary_task = asyncio.create_task(reranker.rerank(query, doc_texts, top_n=len(doc_texts)))
                trans_task = asyncio.create_task(reranker.rerank(secondary_probe, doc_texts, top_n=len(doc_texts)))
                try:
                    reranked_primary = await primary_task
                except Exception:
                    trans_task.cancel()
                    raise
                try:
                    reranked_trans = await trans_task
                    rank_map = {r.index: r for r in reranked_primary}
                    for r_trans in reranked_trans:
                        if r_trans.index in rank_map:
                            rank_map[r_trans.index].score = max(rank_map[r_trans.index].score, r_trans.score)
                        else:
                            rank_map[r_trans.index] = r_trans
                    reranked_primary = list(rank_map.values())
                except Exception as ex:
                    logger.warning("Secondary translation rerank skipped: %s", ex)
            else:
                reranked_primary = await reranker.rerank(query, doc_texts, top_n=len(doc_texts))

            RELEVANCE_THRESHOLD = await get_float("search_relevance_threshold", 0.15)
            fallback_ratio = await get_float("search_relevance_fallback_ratio", 0.5)
            relevant_ranks = _select_relevant_ranks(reranked_primary, RELEVANCE_THRESHOLD, fallback_ratio)
        except Exception as e:
            logger.error("Reranker unavailable (%s) — falling back to unranked RRF order: %s", reranker.__class__.__name__, e)
            reranked = False
            relevant_ranks = _unranked_fallback(merged, doc_texts, limit)

        if relevant_ranks:
            vec_cids = {str(r.id) for r in all_vec_rows}
            kw_cids = {str(r.id) for r in kw_rows}
            trgm_cids = {str(r.id) for r in trgm_rows}
            matched_cids = {str(merged[rank_res.index][0]) for rank_res in relevant_ranks if rank_res.index < len(merged)}
            contributing_legs = []
            if matched_cids & vec_cids:
                contributing_legs.append("vector")
            if matched_cids & kw_cids:
                contributing_legs.append("keyword")
            if matched_cids & trgm_cids:
                contributing_legs.append("fuzzy")
            if matched_cids & fact_row_ids:
                contributing_legs.append("structured")
            search_mode = "+".join(contributing_legs) if contributing_legs else "vector+keyword"

    # --- Step 6: TRI-LINGUAL HYDE AUTOMATIC FALLBACK ---
    if not merged or not relevant_ranks:
        logger.info("Direct tri-lingual search returned 0 matches for '%s'. Triggering Tri-Lingual HyDE Fallback...", query)
        hyde_triggered = True
        hyde_snippets = await _generate_trilingual_hyde(query, expanded)
        hypothetical_snippet = " | ".join(hyde_snippets)
        
        if hyde_snippets:
            try:
                hyde_embeddings = await embed_provider.embed(hyde_snippets)
                hyde_vec_rows = []
                for h_emb in hyde_embeddings:
                    h_emb_str = "[" + ",".join(str(f) for f in h_emb) + "]"
                    h_res = await db.execute(vec_sql, {**params, "query_embedding": h_emb_str})
                    hyde_vec_rows.extend(h_res.fetchall())
                
                if hyde_vec_rows:
                    hyde_rrf_scores = {}
                    hyde_docs_map = {}
                    
                    for rank, row in enumerate(hyde_vec_rows):
                        cid = str(row.id)
                        hyde_docs_map[cid] = row
                        hyde_rrf_scores[cid] = hyde_rrf_scores.get(cid, 0) + (1.0 / (k + (rank % 20) + 1))
                    
                    for rank, row in enumerate(kw_rows):
                        cid = str(row.id)
                        hyde_docs_map[cid] = row
                        hyde_rrf_scores[cid] = hyde_rrf_scores.get(cid, 0) + (1.0 / (k + rank + 1))
                    
                    hyde_merged = sorted(hyde_rrf_scores.items(), key=lambda x: x[1], reverse=True)[:20]
                    if hyde_merged:
                        reranker = get_rerank_provider(override=rerank_provider)
                        doc_texts = [hyde_docs_map[cid].content for cid, _ in hyde_merged]

                        try:
                            # Same cross-script merge fix as the direct-search
                            # pass above — see that comment for the root cause.
                            # Same primary/translated concurrency fix too —
                            # see the direct-search pass's asyncio comment.
                            hyde_secondary_probe = _pick_secondary_rerank_probe(query, expanded)
                            if hyde_secondary_probe:
                                primary_task = asyncio.create_task(reranker.rerank(query, doc_texts, top_n=len(doc_texts)))
                                trans_task = asyncio.create_task(reranker.rerank(hyde_secondary_probe, doc_texts, top_n=len(doc_texts)))
                                try:
                                    reranked_primary = await primary_task
                                except Exception:
                                    trans_task.cancel()
                                    raise
                                try:
                                    reranked_trans = await trans_task
                                    rank_map = {r.index: r for r in reranked_primary}
                                    for r_trans in reranked_trans:
                                        if r_trans.index in rank_map:
                                            rank_map[r_trans.index].score = max(rank_map[r_trans.index].score, r_trans.score)
                                        else:
                                            rank_map[r_trans.index] = r_trans
                                    reranked_primary = list(rank_map.values())
                                except Exception as ex:
                                    logger.warning("Secondary translation HyDE rerank skipped: %s", ex)
                            else:
                                reranked_primary = await reranker.rerank(query, doc_texts, top_n=len(doc_texts))

                            RELEVANCE_THRESHOLD = await get_float("search_relevance_threshold", 0.15)
                            fallback_ratio = await get_float("search_relevance_fallback_ratio", 0.5)
                            # Same near-miss fallback as the direct-search pass above (see
                            # _select_relevant_ranks) -- this is HyDE's own rerank, already a
                            # fallback path itself, so a near-miss here is just as worth
                            # surfacing as one on the primary pass rather than exhausting
                            # every fallback and still landing on "no matches".
                            relevant_ranks = _select_relevant_ranks(reranked_primary, RELEVANCE_THRESHOLD, fallback_ratio)
                        except Exception as e:
                            logger.error("Reranker unavailable (%s) — falling back to unranked RRF order: %s", reranker.__class__.__name__, e)
                            reranked = False
                            # hyde_merged, not merged: on this path doc_texts was
                            # built from the HyDE candidate set, so the scores have
                            # to come from the same list the texts are indexed against.
                            relevant_ranks = _unranked_fallback(hyde_merged, doc_texts, limit)

                        if relevant_ranks:
                            merged = hyde_merged
                            docs_map = hyde_docs_map
                            search_mode = "HyDE"
                            hyde_success = True
                            logger.info("Tri-Lingual HyDE Fallback SUCCESS: Found %d matching candidate(s)", len(relevant_ranks))
                        else:
                            logger.info("HyDE candidates scored below relevance threshold (%.2f). Yielding 0 results.", RELEVANCE_THRESHOLD)
            except Exception as e:
                logger.error("Tri-Lingual HyDE fallback vector search failed: %s", e)

    # If still no matches after Direct + HyDE Fallback
    if not merged or not relevant_ranks:
        if hyde_triggered:
            search_mode = "failed_all"
            hyde_success = False
            
        if filters and "document_id" in filters:
            doc_id_param = str(filters["document_id"])
            try:
                target_doc_id = UUID(doc_id_param)
            except ValueError:
                target_doc_id = None

            if target_doc_id:
                chunk_sql = text("""
                    SELECT c.id, c.content, c.page_number, c.chunk_index, d.title, d.id as doc_id, v.s3_path
                    FROM doc_dg_chunks c
                    JOIN doc_dg_documents d ON c.document_id = d.id
                    LEFT JOIN doc_dg_document_versions v ON v.id = d.current_version_id
                    WHERE d.id = :target_doc_id AND d.tenant_id = CAST(:tenant_id AS uuid) AND d.is_trashed = false
                    ORDER BY c.page_number ASC, c.chunk_index ASC
                    LIMIT 30
                """)
                chunk_res = await db.execute(chunk_sql, {"target_doc_id": target_doc_id, "tenant_id": str(tenant_id)})
                doc_chunk_rows = chunk_res.fetchall()

                excerpts = []
                fallback_results = []

                if doc_chunk_rows:
                    for r in doc_chunk_rows:
                        s3_path = r.s3_path
                        url = await generate_presigned_url(s3_path) if s3_path else ""
                        fallback_results.append(SearchResult(
                            document_id=r.doc_id,
                            document_name=r.title,
                            download_url=url,
                            page_number=r.page_number,
                            snippet=_make_snippet(r.content),
                            score=1.0,
                            metadata={}
                        ))
                        excerpts.append({
                            "document_id": r.doc_id,
                            "document_name": r.title,
                            "page_number": r.page_number,
                            "chunk_id": r.id,
                            "content": r.content,
                        })

                if excerpts:
                    citations = []
                    doc_grounded = True
                    if not generate_summary:
                        summary = (
                            "AI summary generation is disabled for this search (testing mode). "
                            "Raw retrieved excerpts are shown below."
                        )
                    else:
                        try:
                            summary, citations, doc_grounded = await _generate_grounded_answer(query, response_lang, excerpts)
                            if summary is None:
                                summary = "The document does not contain information that answers this question."
                            else:
                                doc_url_by_id = {r.document_id: r.download_url for r in fallback_results}
                                for c in citations:
                                    c.download_url = doc_url_by_id.get(c.document_id)
                        except Exception as e:
                            logger.warning("AI summary unavailable for document preview: %s", e)
                            summary = (
                                f"Found {len(fallback_results)} matching page(s) for '{query}'. "
                                "AI summary is temporarily unavailable — the excerpts below are unedited source text."
                            )
                            doc_grounded = False

                    took_ms = int((time.time() - start_time) * 1000)
                    resp = SearchResponse(
                        query=query,
                        ai_summary=summary,
                        results=fallback_results,
                        citations=citations,
                        refused=not doc_grounded,
                        cached=False,
                        took_ms=took_ms,
                        search_mode=search_mode,
                        hyde_triggered=hyde_triggered,
                        reranked=True,
                        grounded=doc_grounded
                    )
                    await log_action(
                        db,
                        user_id,
                        tenant_id,
                        "search.query",
                        details={
                            "query": query,
                            "search_mode": search_mode,
                            "hyde_triggered": hyde_triggered,
                            "hyde_success": hyde_success,
                            "hypothetical_snippet": hypothetical_snippet,
                            "result_count": len(fallback_results),
                            "took_ms": took_ms
                        },
                        ip_address=ip_address
                    )
                    return resp

        took_ms = int((time.time() - start_time) * 1000)

        # T74: a document must be findable by metadata before indexing
        # finishes — try a title match against still-processing documents
        # before falling back to a generic "nothing found" message.
        title_matches = await _find_pending_title_matches(db, tenant_id, query, exclude_doc_ids=set())

        if title_matches:
            summary_text = (
                f"Found {len(title_matches)} matching document(s) by name for '{query}', "
                f"still being indexed — full-text search will include them shortly."
            )
        else:
            pending_preview_limit = await get_int("search_pending_docs_preview_limit", 3)
            pending_sql = text(f"SELECT title FROM doc_dg_documents WHERE tenant_id = CAST(:tenant_id AS uuid) AND status IN ('pending', 'processing') AND is_trashed = false LIMIT {pending_preview_limit}")
            pending_res = await db.execute(pending_sql, {"tenant_id": str(tenant_id)})
            pending_titles = [r.title for r in pending_res.fetchall()]

            if pending_titles:
                titles_str = ", ".join([f"'{t}'" for t in pending_titles])
                summary_text = (
                    f"No indexed matches found for '{query}'.\n\n"
                    f"ℹ️ **AI Indexing Notice**: {len(pending_titles)} document(s) ({titles_str}) are currently being processed in the background (OCR, text chunking, and 1024d vector embedding generation). Please wait a few seconds for indexing to finish and search again."
                )
            else:
                summary_text = f"No matching documents were found in your drive for '{query}'."

        resp = SearchResponse(
            query=query,
            ai_summary=summary_text,
            results=title_matches,
            cached=False,
            took_ms=took_ms,
            search_mode=search_mode,
            hyde_triggered=hyde_triggered,
            reranked=True,
            grounded=True
        )
        await log_action(
            db,
            user_id,
            tenant_id,
            "search.query",
            details={
                "query": query,
                "search_mode": search_mode,
                "hyde_triggered": hyde_triggered,
                "hyde_success": hyde_success,
                "hypothetical_snippet": hypothetical_snippet,
                "result_count": len(title_matches),
                "took_ms": took_ms
            },
            ip_address=ip_address
        )
        return resp
        
    _mark("rerank")

    final_results = []
    excerpts = []
    doc_ids_for_metadata = []
    seen_dedup = set()

    # Real bug found live (verification report, 2026-09-09): a chat
    # answer's citation pointed at a different document than the one
    # named in the user's own question, because the corpus has
    # near-duplicate scans of the same underlying register and retrieval
    # silently pulled a chunk from the highest-scoring duplicate instead.
    # Resolve every distinct candidate document (already in relevance-rank
    # order) down to its duplicate-cluster representative BEFORE either
    # dedup pass below, so a lower-ranked near-duplicate's chunks never
    # reach final_results/excerpts at all — see
    # duplicate_service.resolve_duplicate_representatives for the full
    # reasoning and why this is the structural fix, not a prompt patch.
    doc_ids_by_rank: List[UUID] = []
    seen_doc_ids = set()
    for rank_res in relevant_ranks:
        doc_id = docs_map[merged[rank_res.index][0]].doc_id
        if doc_id not in seen_doc_ids:
            seen_doc_ids.add(doc_id)
            doc_ids_by_rank.append(doc_id)
    duplicate_representative = await resolve_duplicate_representatives(db, tenant_id, doc_ids_by_rank)

    for rank_res in relevant_ranks:
        idx = rank_res.index
        cid, _ = merged[idx]
        row = docs_map[cid]

        # A document that isn't its own cluster's representative is a
        # near-duplicate of one already ranked higher — skip its chunks
        # entirely rather than let a redundant, confusingly-different
        # document_id show up alongside the one actually worth surfacing.
        if duplicate_representative.get(row.doc_id, row.doc_id) != row.doc_id:
            continue

        # Deduplicate identical document page matches — except a fact
        # result (T73), which never dedupes against a chunk (or another
        # fact) sharing its page: it's a distinct extracted field, not a
        # near-duplicate snippet the way two chunks on the same page are.
        dedup_key = (row.doc_id, cid) if cid in fact_row_ids else (row.doc_id, row.page_number)
        if dedup_key in seen_dedup:
            continue
        seen_dedup.add(dedup_key)

        doc_ids_for_metadata.append(row.doc_id)

        if len(final_results) >= limit:
            break
            
    # Query metadata for returned documents (Task 6.4)
    meta_map = {}
    if doc_ids_for_metadata:
        stmt = select(MetadataItem).where(MetadataItem.document_id.in_(doc_ids_for_metadata))
        meta_res = await db.execute(stmt)
        for m in meta_res.scalars().all():
            doc_id = m.document_id
            if doc_id not in meta_map:
                meta_map[doc_id] = {}
            val = m.value
            if isinstance(val, dict) and "v" in val:
                val = val["v"]
            meta_map[doc_id][m.key] = val
            
    seen_dedup.clear()
    seen_excerpt_keys = set()
    for rank_res in relevant_ranks:
        idx = rank_res.index
        cid, _ = merged[idx]
        row = docs_map[cid]
        is_fact = cid in fact_row_ids

        # Same near-duplicate skip as the metadata pre-pass above — keeps
        # a lower-ranked duplicate's content out of both final_results
        # AND the excerpts the grounding LLM (and its citations) actually
        # see, which is where the reported bug (a citation pointing at a
        # confusing near-duplicate document) actually happened.
        if duplicate_representative.get(row.doc_id, row.doc_id) != row.doc_id:
            continue

        # Excerpts feed the grounding LLM (T70) — deduped only by literal
        # chunk/fact identity, never by page, unlike final_results below.
        # TextChunker's 64/512-token overlap means two chunks sharing a
        # page are mostly DISTINCT content, not near-duplicates the way
        # page-level dedup assumes for the results list — collapsing them
        # here could silently starve the grounding LLM of the one chunk
        # that actually answers the question. Live bug, 2026-09-04: a
        # village-record page's two page-1 chunks each carried different
        # header fields (village in one, district in the other); page
        # dedup kept only whichever the RRF ranked slightly higher and
        # fed just that one to the LLM, so a district question got no
        # answer even though the district chunk was retrieved and ranked.
        excerpt_key = (row.doc_id, cid)
        if excerpt_key not in seen_excerpt_keys and len(excerpts) < limit:
            seen_excerpt_keys.add(excerpt_key)
            excerpts.append({
                "document_id": row.doc_id,
                "document_name": row.title,
                "page_number": row.page_number,
                "chunk_id": None if is_fact else row.id,
                "fact_id": row.id if is_fact else None,
                "content": row.content,
            })

        # T73 — a fact result never dedupes against a chunk (or another
        # fact) sharing its page: it's a distinct extracted field, not a
        # near-duplicate snippet the way two chunks on the same page are.
        # The results LIST (unlike excerpts above) intentionally keeps
        # page-level dedup for chunks: one representative snippet per
        # page keeps a small `limit` from being crowded out by a single
        # page's multiple chunks, preserving diversity across pages/docs.
        dedup_key = (row.doc_id, cid) if is_fact else (row.doc_id, row.page_number)
        if dedup_key in seen_dedup:
            if len(final_results) >= limit and len(excerpts) >= limit:
                break
            continue
        seen_dedup.add(dedup_key)

        s3_path = row.s3_path
        url = await generate_presigned_url(s3_path) if s3_path else ""

        # T73 — a fact-leg row shares docs_map/RRF with chunk rows (same
        # column shape) but is cited by fact_id, not chunk_id: it points
        # at one extracted field, not a page of running text.
        result_metadata = dict(meta_map.get(row.doc_id, {}))
        if is_fact:
            result_metadata["fact_id"] = cid

        final_results.append(SearchResult(
            document_id=row.doc_id,
            document_name=row.title,
            download_url=url,
            page_number=row.page_number,
            snippet=_make_snippet(row.content),
            score=rank_res.score,
            metadata=result_metadata
        ))

        if len(final_results) >= limit and len(excerpts) >= limit:
            break

    # T74: also surface any still-processing documents whose title matches —
    # findable by metadata immediately, not just once indexing finishes.
    #
    # Real latency fix, 2026-09-10 (QA report #6): this is a plain DB lookup,
    # independent of the grounded-answer LLM call below (citations only ever
    # reference documents already in final_results/excerpts before this
    # extend — a still-processing title match was never eligible to be an
    # excerpt) — kick it off now and only await it once we actually need the
    # combined list, so it overlaps the LLM round-trip instead of preceding it.
    already_found = {r.document_id for r in final_results}
    pending_title_task = asyncio.create_task(_find_pending_title_matches(db, tenant_id, query, exclude_doc_ids=already_found))

    _mark("assemble-results")

    # Hand the finished results to a progressive caller before spending the
    # ~3s on the grounded answer. Best-effort: a consumer that has gone away
    # (client disconnected mid-stream) must not abort a search that is
    # otherwise about to succeed and be cached.
    if on_results is not None:
        try:
            await on_results(list(final_results))
        except Exception as e:
            logger.warning("on_results callback failed, continuing search: %s", e)

    # 7. Generate the AI answer — T70: every claim bound to a source excerpt,
    # refuse outright rather than guess when the excerpts don't answer it.
    citations = []
    refused = False
    grounded = True
    if not final_results or not excerpts:
        refusal_msg = await get_str("search_refusal_message", "The corpus does not say.")
        summary = refusal_msg
        refused = True
        citations = []
        grounded = False
    elif not generate_summary:
        summary = (
            f"Found {len(final_results)} matching document(s) for '{query}'. "
            "AI summary generation is disabled for this search (testing mode)."
        )
    else:
        try:
            summary, citations, grounded = await _generate_grounded_answer(query, response_lang, excerpts)
            if summary is None:
                refusal_msg = await get_str("search_refusal_message", "The corpus does not say.")
                summary = refusal_msg
                refused = True
                citations = []
            else:
                doc_url_by_id = {r.document_id: r.download_url for r in final_results}
                for c in citations:
                    c.download_url = doc_url_by_id.get(c.document_id)
        except Exception as e:
            logger.warning("AI summary unavailable: %s", e)
            summary = (
                f"Found {len(final_results)} matching document(s) for '{query}'. "
                "AI summary is temporarily unavailable — the excerpts below are unedited source text."
            )
            grounded = False

    final_results.extend(await pending_title_task)
    _mark("rerank+summary")

    took_ms = int((time.time() - start_time) * 1000)
    if _phase_marks:
        logger.info(
            "search timing: total=%dms | %s | mode=%s reranked=%s hyde=%s",
            took_ms,
            " ".join(f"{label}={secs * 1000:.0f}ms" for label, secs in _phase_marks),
            search_mode, reranked, hyde_triggered,
        )

    resp = SearchResponse(
        query=query,
        ai_summary=summary,
        results=final_results,
        citations=citations,
        refused=refused,
        cached=False,
        took_ms=took_ms,
        search_mode=search_mode,
        hyde_triggered=hyde_triggered,
        reranked=reranked,
        grounded=grounded
    )

    # Audit log & Cache
    await log_action(
        db,
        user_id,
        tenant_id,
        "search.query",
        details={
            "query": query,
            "search_mode": search_mode,
            "hyde_triggered": hyde_triggered,
            "hyde_success": hyde_success,
            "hypothetical_snippet": hypothetical_snippet,
            "result_count": len(final_results),
            "took_ms": took_ms
        },
        ip_address=ip_address
    )
    cache_ttl = await get_int("search_cache_ttl_seconds", 300)
    await cache_search_result(cache_key, resp, ttl=cache_ttl)
    
    return resp
