# T09 — Technical Architecture Document (Decision D2)

Status: **signed — 2026-08-24**
Blocks (now unblocked): T10 (entity graph schema) and everything cascading from it
Owner: signed off by the project decision-maker on 2026-08-24. The Apache AGE evaluation formalizes a decision the build design doc already states ("Postgres tables for the graph... do not re-open it") — nothing here is a new judgment call.
Reference: Build Design v0.3 Section 11 ("Two ways we install it"); docs/planning/backlog.txt T09

---

## Scope

Four things this document has to settle, per the backlog: both deployment profiles, the egress-zero approach, model abstraction, and an Apache AGE evaluation for the entity graph. This formalizes decisions the build design doc already states, plus one new evaluation (Apache AGE) that hadn't been written up.

---

## 1. Two deployment profiles

**SaaS profile** — external APIs (Groq/OpenAI/Anthropic for LLM, Cohere for rerank), metered by usage. Embeddings already run locally (BGE-M3). This is what's running today.

**Air-gapped profile** — a single network with zero outbound calls. LLM and VLM/OCR run as local models (reference: Qwen2.5-VL-7B-Instruct on a 24GB GPU); rerank and embeddings run locally too. Currently **not implemented** — no local LLM/VLM provider exists in the codebase (`app/ai/factory.py`, `app/ocr/factory.py` currently only wire up SaaS/API-based providers: Groq, Cohere, pdfplumber, LlamaParse).

Both profiles share the same four provider interfaces (below) — a feature never depends on which profile is running, only which concrete provider is configured behind the interface.

## 2. Egress-zero approach

The air-gapped profile's hard rule: a missing local model **raises an error and stops**, it never silently calls an external API. This is a fail-closed requirement, checked by a CI egress-monitor job (T92, not yet built) asserting zero outbound connections with the network blocked.

**Finding, worth flagging now:** the existing provider abstraction (`app/ai/factory.py`) already implements `FallbackLLMProvider` / `FallbackEmbeddingProvider` — primary provider fails, falls back to a secondary. This pattern is correct for the **SaaS profile** (resilience across cloud vendors) but is the **opposite** of what air-gapped mode requires (no fallback to an external API, ever). When the local-model providers are built (T91+), the air-gapped configuration must NOT wire through `FallbackLLMProvider` — it needs a distinct "local-only, raise on failure" provider path. This is a real gap between the current code and the air-gapped requirement, not yet built either way.

## 3. Model abstraction

Already in place and confirmed working: `app/ai/base.py` defines `LLMProvider`, `EmbeddingProvider`, `RerankerProvider` as abstract interfaces; `app/ocr/base.py`-equivalent (`OCRProvider`) does the same for OCR/extraction. `app/ai/factory.py` and `app/ocr/factory.py` resolve the concrete implementation from config (`settings.ai_ocr_provider`, etc.) — swapping providers is a config change, not a code change. This satisfies the "no AI vendor lock-in" requirement already; local-model implementations plug into the same interfaces when built.

## 4. Apache AGE evaluation (for T10, entity graph)

**Decision: plain Postgres tables (`entity_dg_nodes`, `entity_dg_edges`), not Apache AGE.**

Apache AGE is a Postgres extension adding openCypher graph-query support. Considered and rejected for v1:

- **Deployment surface.** The air-gapped profile ships into government/enterprise networks where every new dependency needs separate vetting. AGE is an additional Postgres extension (its own install, version-compatibility, and backup/restore story) on top of an already-complex air-gapped bundle. Plain tables need nothing beyond Postgres itself, which is already a hard dependency.
- **Query shape doesn't need it.** The entity graph's actual access patterns — filter edges by type/tier/status/confidence for a given node, bulk-update edges above a threshold, walk 1–2 hops (entity → document, entity → entity via a typed edge) — are exactly what indexed relational joins do well. Nothing in the spec calls for deep recursive graph traversal (arbitrary-depth pathfinding, centrality, community detection) that would actually benefit from a native graph engine.
- **Operational uniformity.** The rest of the stack is plain SQLAlchemy + Postgres (including pgvector for embeddings). Adding Cypher as a second query language for one module increases the surface a new engineer needs to learn, and a second driver/connection path to maintain, for a benefit the query patterns don't require.

This matches what the build design doc already states elsewhere ("Use Postgres tables for the graph. That decision is already recorded — do not re-open it") — this section is the formal write-up of *why*, which is what T09 asked for.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree | 2026-08-24 |

Signed. T10 (entity_dg_nodes / entity_dg_edges schema) is unblocked.
