# Search latency

Measured on this stack, 2026-09-21. Numbers come from the `search timing`
log line (`app.services.search_service`), not estimates.

## Where the time goes

| Phase | Cold | Warm (cached) | What it is |
|---|---|---|---|
| expand + glossary | 1725 ms | **4 ms** | Groq LLM: query → English/Hindi/Marathi variants |
| embed variants | 1095 ms | **~400 ms** | BGE-M3, local, ~8 variants |
| retrieval legs | 529 ms | ~460 ms | Postgres: vector + keyword + trigram + facts |
| rerank | 1449 ms | ~900 ms | Cohere, **two** calls (see below) |
| assemble | 40 ms | ~50 ms | snippets, presigned URLs |
| grounded summary | 1184 ms | 3121–3482 ms | Groq LLM, highly variable |
| **total** | **6020 ms** | **~5000 ms** | |

Deterministic pipeline work (expand + embed) dropped from ~2.8 s to ~0.4 s.
What remains is almost entirely waiting on three external API calls.

## Two caches, and why they are safe

Both cache a **pure function of the query text** — no tenant data, no filters,
no documents — so they are global rather than tenant-scoped, and the second
person to ask a question benefits from the first person's round-trip.

- **Query expansion** (`qexpand:`, 24 h). Same query → same English/Hindi/
  Marathi forms. Only *successful* expansions are cached: the call-failed
  fallback returns the query unchanged in all three languages, and caching
  that would pin a degraded monolingual expansion for 24 h, silently losing
  the cross-script matching this corpus depends on.
- **Query embeddings** (`qembed:`, 24 h). Same text → same vector. Only the
  variants missing from cache are embedded; results are recombined in the
  original variant order because everything downstream indexes `q_embeddings`
  positionally against `tri_queries`.

The existing full-response cache is unchanged and stays tenant-scoped — it
holds answers grounded in a tenant's documents, which must never cross
tenants.

## The real ceiling: free-tier rate limits

Over one 30-minute window of testing: **33 HTTP 429s** (15 Cohere, 4 Groq).

- **Cohere rerank is the binding constraint.** The trial key allows 10 calls
  per minute and each search makes **two**, so roughly **five searches per
  minute** saturates the quota. Beyond that, rerank climbs from ~900 ms to
  2.7 s, 3.9 s, 7.2 s.
- **Groq summary is the most variable single phase**, measured between 1.2 s
  and 56 s on identical work. That variance is throttling, not the prompt.

No amount of caching fixes this: a novel query must call all three APIs. A
paid Cohere key and a paid Groq tier are the remaining lever, and they are a
purchasing decision rather than a code change.

## Why the two rerank calls cannot be reduced to one

The obvious optimisation — drop the secondary cross-script rerank probe and
halve Cohere usage — was tested and **rejected on the evidence**. Reranking
the same candidates with the English probe alone, against this Marathi
corpus:

| English query | EN probe only | EN + MR probe |
|---|---|---|
| survey number valuation | **0 results** | 2 |
| graveyard area measurement | **0 results** | 1 |
| trust administration scheme | **0 results** | 1 |
| masjid land parcel | **0 results** | 1 |
| wakf property list | 1 | 1 |

On four of five English queries the second probe is the only reason anything
is found at all. The documents are Marathi; an English probe scores them near
zero. Halving the API calls would have halved the latency and destroyed
recall.

## What would actually help next

1. **Paid Cohere key** — removes the binding constraint. Largest single win.
2. **Stream the summary.** It is now the biggest block (~3 s) and the user
   waits for the whole response. Streaming tokens changes perceived latency
   far more than shaving milliseconds, but it is an API contract change.
3. **Skip the summary when the caller does not need it.** `generate_summary`
   already exists; a "results only" mode would return in ~1.5 s warm.
4. **A local reranker was measured and rejected** — the bundled BGE-M3
   reranker ran >10 minutes on 20 real chunks at 194% CPU and 4.5 GB, versus
   Cohere's ~0.5 s network call. It is not viable on this hardware.
