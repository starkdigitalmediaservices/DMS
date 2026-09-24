import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
from .config import settings
from .api.v1.router import api_router
from .services.cache_service import init_redis
from .tasks.worker import celery_app

from .services.storage_service import ensure_bucket_exists, ensure_archive_bucket_exists
from .services.connector_base import get_enabled_connectors
from .api_logging_middleware import ApiLoggingMiddleware

from .limiter import limiter
import asyncio

# Nothing in this app ever configured logging, so the root logger sat at
# its WARNING default and every logger.info() in the codebase was silently
# discarded in the running service — connector "ingested X" lines, search
# phase timings, ingestion progress, all of it. Only warnings and errors
# ever reached the container logs, which meant the instrumentation that
# already existed was unusable for diagnosing anything in production.
# Configured once here, at import time, before the app starts.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
# SQLAlchemy's engine logger is separately very chatty (it echoes every
# statement and every bound parameter list, which is what buried the
# useful lines in these logs); keep it at WARNING unless explicitly asked.
logging.getLogger("sqlalchemy.engine").setLevel(
    getattr(logging, settings.sql_log_level.upper(), logging.WARNING)
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await ensure_bucket_exists()
    try:
        # T64/T93 — previously a manual runbook step ("Known gap to check
        # manually"); the archive bucket must exist WITH Object Lock before
        # any WORM archival call, and Object Lock can only be set at bucket
        # creation, so this has to run before anything else touches it.
        # Best-effort: WORM archival is an auxiliary evidence feature, not
        # core to the app serving traffic — a hiccup here must never block
        # startup the way a missing operational-documents bucket would.
        await ensure_archive_bucket_exists()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"T64 WORM archive bucket setup failed at startup: {e}")
    # Warm the local embedding model before serving traffic. BGE-M3 loads
    # lazily on first use and that load is ~17s on CPU (measured: first
    # embed 17.35s, every subsequent one 0.22s), which the first real
    # search or upload after every deploy/restart was paying in full.
    # get_embed_provider() caches a module-level singleton, so doing one
    # throwaway embed here moves that cost into startup where nobody is
    # waiting on it. Run in a thread so the model load doesn't block the
    # event loop, and best-effort so a warmup hiccup can't stop the app
    # from booting — the first request would just pay the load as before.
    async def _warm_embedding_model():
        import logging
        import time
        log = logging.getLogger(__name__)
        try:
            from .ai.factory import get_embed_provider
            started = time.time()
            await get_embed_provider().embed(["warmup"])
            log.info("Embedding model warm after %.1fs", time.time() - started)
        except Exception as e:
            log.warning("Embedding warmup failed (first request will pay the load): %s", e)

    warmup_task = asyncio.create_task(_warm_embedding_model())

    # T40 — connectors are a typed contract (services/connector_base.py);
    # a new connector is added to get_enabled_connectors(), never here.
    connector_tasks = [asyncio.create_task(c.run_loop()) for c in get_enabled_connectors()]
    yield
    warmup_task.cancel()
    for task in connector_tasks:
        task.cancel()


class _UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    """Real bug found live 2026-09-09: an unhandled exception (any 500 not
    raised as a FastAPI/Starlette HTTPException) never got CORS headers,
    even though 404s/422s/other HTTPExceptions always did -- confirmed live,
    reproduced a bare 500 with zero Access-Control-* headers on it.

    Root cause is structural, not a missing handler: Starlette's
    build_middleware_stack() special-cases a handler registered for the
    bare Exception class (or status 500) to run in ServerErrorMiddleware,
    which it places OUTSIDE every middleware added via add_middleware() --
    CORSMiddleware included -- specifically so an error page can still
    render even if a broken middleware itself is what's crashing. That
    means @app.exception_handler(Exception) can never fix this: it's
    wired to the one place a response can't flow back out through CORS.

    The only way to get a caught exception's response through CORS is to
    catch it INSIDE a real middleware positioned inside CORSMiddleware's
    wrap -- turning it into a normal Response before it ever reaches
    ServerErrorMiddleware. This class must be added to the app BEFORE
    CORSMiddleware (see create_app) so CORSMiddleware ends up wrapping it,
    not the other way around."""

    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logging.getLogger(__name__).exception(
                f"Unhandled exception on {request.method} {request.url.path}"
            )
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def create_app() -> FastAPI:
    app = FastAPI(
        title='Document Search Engine',
        description='Multi-tenant AI document search and retrieval platform',
        version='1.0.0',
        lifespan=lifespan,
        docs_url='/api/docs',
        redoc_url='/api/redoc',
    )
    
    # Added before CORSMiddleware so it ends up wrapped BY it (Starlette
    # inserts each later add_middleware() call at the outer edge) -- see
    # _UnhandledExceptionMiddleware's own docstring for why that ordering
    # is what actually makes CORS headers reach a 500 response.
    app.add_middleware(_UnhandledExceptionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API call logging middleware (runs after CORS)
    app.add_middleware(ApiLoggingMiddleware)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.include_router(api_router)
    
    # Attach Celery app to the FastAPI app instance
    app.celery_app = celery_app
    
    return app

app = create_app()