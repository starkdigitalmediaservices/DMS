import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text

from ...database import AppSessionLocal
from ...deps import (
    get_tenant_db, require_tenant_access, get_request_ip,
    _reset_session_tenant_context,
)
from ...schemas.auth import TokenPayload
from ...schemas.search import SearchRequest, SearchResponse
from ...services.search_service import search as do_search

router = APIRouter()


@router.post('/', response_model=SearchResponse)
async def search(
    body: SearchRequest,
    request: Request,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    ip_addr = await get_request_ip(request)

    return await do_search(
        query=body.query,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=body.limit,
        filters=body.filters,
        db=db,
        ip_address=ip_addr,
        rerank_provider=body.rerank_provider,
        generate_summary=body.generate_summary
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post('/stream')
async def search_stream(
    body: SearchRequest,
    request: Request,
    current_user: TokenPayload = Depends(require_tenant_access),
):
    """Same search, delivered in two parts over SSE.

    Measured warm, the documents are ready at ~1.8s and the grounded answer
    lands at ~5s, because the answer costs an LLM round-trip on top of
    retrieval. The non-streaming endpoint makes the user wait for the slower
    half before seeing either. This emits:

        event: results   the ranked documents, as soon as they exist
        event: summary   the grounded answer + citations, when verified
        event: done      timings and flags
        event: error     something failed; the stream ends

    Why the ANSWER is not streamed token by token: it is not free-form prose.
    _generate_grounded_answer asks the model for structured claims, then
    verifies each one (every number in a claim must appear in the excerpt it
    cites), DROPS the claims that fail, and numbers citations only once the
    surviving set is known — and if nothing survives, the whole answer becomes
    a refusal. Streaming raw tokens would emit JSON rather than readable text,
    show claims that verification is about to discard, and could show an
    answer that ends up being retracted. That trades the product's grounding
    guarantee for perceived speed. Streaming per VERIFIED CLAIM is compatible
    with this design and is the natural next step; it needs streaming support
    in the LLM provider, which does not exist yet (providers expose complete()
    only).
    """
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    ip_addr = await get_request_ip(request)

    async def event_stream():
        # This generator runs AFTER FastAPI has torn down the endpoint's
        # dependencies, so a session injected via Depends(get_tenant_db) is
        # already closed and its app.current_tenant_id already reset by the
        # time any of this executes — every write then fails RLS (confirmed:
        # the audit insert for search.query was rejected outright). The
        # stream therefore owns its session for its whole lifetime, set up
        # exactly as get_tenant_db does.
        async with AppSessionLocal() as db:
            await db.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"),
                {"t": str(tenant_id)},
            )
            try:
                async for chunk in _run(db):
                    yield chunk
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            finally:
                await _reset_session_tenant_context(db)

    async def _run(db: AsyncSession):
        queue: asyncio.Queue = asyncio.Queue()

        async def on_results(results):
            await queue.put(results)

        task = asyncio.create_task(do_search(
            query=body.query,
            tenant_id=tenant_id,
            user_id=user_id,
            limit=body.limit,
            filters=body.filters,
            db=db,
            ip_address=ip_addr,
            rerank_provider=body.rerank_provider,
            generate_summary=body.generate_summary,
            on_results=on_results,
        ))

        # Race the callback against the search finishing: a cache hit returns
        # without ever invoking on_results, and an early failure must not hang
        # the client waiting for a result set that is never coming.
        queue_get = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait({queue_get, task}, return_when=asyncio.FIRST_COMPLETED)

        if queue_get in done:
            results = queue_get.result()
            yield _sse("results", {
                "results": [r.model_dump() for r in results],
                "count": len(results),
            })
        else:
            queue_get.cancel()

        try:
            response: SearchResponse = await task
        except Exception as e:
            yield _sse("error", {"detail": str(e)})
            return

        # A cached response skips the callback entirely, so its results have
        # not been sent yet — send them now rather than leaving the client
        # with only a summary.
        if queue_get.cancelled() or queue_get not in done:
            yield _sse("results", {
                "results": [r.model_dump() for r in response.results],
                "count": len(response.results),
            })

        yield _sse("summary", {
            "ai_summary": response.ai_summary,
            "citations": [c.model_dump() for c in (response.citations or [])],
            "grounded": response.grounded,
            "refused": response.refused,
        })
        yield _sse("done", {
            "took_ms": response.took_ms,
            "search_mode": response.search_mode,
            "reranked": response.reranked,
            "hyde_triggered": response.hyde_triggered,
            "cached": response.cached,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this an nginx/ingress in front buffers the whole
            # response and the progressive delivery silently becomes a
            # single slow reply again.
            "X-Accel-Buffering": "no",
        },
    )
