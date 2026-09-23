import time
import uuid
import logging
import asyncio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.database import AsyncSessionLocal
from app.models.api_log import ApiLog

logger = logging.getLogger(__name__)

# Live incident 2026-09-23: this middleware deadlocked the database for 30+
# minutes. pg_stat_activity showed sessions stuck `idle in transaction` on
# INSERT INTO audit_dg_api_logs, the oldest 36 minutes old, holding locks
# that six other statements were queued behind -- every test teardown
# (DELETE FROM iam_dg_users, UPDATE doc_dg_documents) blocked, which looked
# like hung test runs rather than a database problem.
#
# The write was fired with a bare `asyncio.create_task(...)` and the Task
# was thrown away. asyncio keeps only a weak reference to a running task, so
# it could be collected mid-await -- between the INSERT and the COMMIT the
# connection is left inside a transaction and the async context manager's
# cleanup never runs. Three guards, since this is best-effort telemetry that
# must never be able to block real work:

# Hold a strong reference for the task's lifetime so it cannot be collected
# mid-transaction.
_pending_log_tasks: set[asyncio.Task] = set()

# A write that never completes must not hold its transaction open forever.
LOG_WRITE_TIMEOUT_SECONDS = 5.0

# A traffic burst must not open unbounded concurrent transactions. Past this,
# drop the log line -- losing telemetry is strictly better than exhausting the
# connection pool with writes nobody is waiting on.
MAX_IN_FLIGHT_LOG_WRITES = 50


def _spawn_log_task(coro) -> "asyncio.Task | None":
    """Start a log write, keeping it referenced and bounded."""
    if len(_pending_log_tasks) >= MAX_IN_FLIGHT_LOG_WRITES:
        coro.close()
        logger.warning(
            "Dropping API log write: %d already in flight (cap %d)",
            len(_pending_log_tasks), MAX_IN_FLIGHT_LOG_WRITES,
        )
        return None
    task = asyncio.create_task(coro)
    _pending_log_tasks.add(task)
    task.add_done_callback(_pending_log_tasks.discard)
    return task


async def _write_log(**kwargs):
    try:
        # The timeout wraps the session context manager, not just the commit,
        # so a cancellation still unwinds through __aexit__ and releases the
        # connection instead of abandoning it mid-transaction.
        async with asyncio.timeout(LOG_WRITE_TIMEOUT_SECONDS):
            async with AsyncSessionLocal() as session:
                session.add(ApiLog(**kwargs))
                await session.commit()
    except asyncio.CancelledError:
        # The caller (shutdown) is cancelling us, not our own timeout --
        # never swallow that.
        raise
    except TimeoutError:
        logger.warning(
            "API log write exceeded %ss and was abandoned: %s %s",
            LOG_WRITE_TIMEOUT_SECONDS, kwargs.get("method"), kwargs.get("path"),
        )
    except Exception as e:
        logger.warning("Failed to log API call: %s", e)


class ApiLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs every API request to the api_logs table."""

    # Paths to skip logging (health checks, static files, docs)
    SKIP_PREFIXES = ("/api/docs", "/api/redoc", "/openapi.json", "/_next", "/static", "/favicon", "/api/v1/health")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip non-API and docs paths
        if any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES):
            return await call_next(request)

        start_time = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Extract user/tenant from Authorization header (best-effort, non-blocking)
        user_id = None
        tenant_id = None
        try:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                from app.services.auth_service import verify_token
                payload = verify_token(token)
                user_id = uuid.UUID(payload.sub)
                tenant_id = uuid.UUID(payload.tenant_id)
        except Exception:
            pass

        # Get IP address
        ip_address = None
        try:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                ip_address = forwarded.split(",")[0].strip()
            elif request.client:
                ip_address = request.client.host
        except Exception:
            pass

        user_agent = request.headers.get("user-agent", "")[:500] if request.headers.get("user-agent") else None

        # Non-blocking, but referenced, bounded and time-limited -- see
        # _spawn_log_task above for why a bare create_task is not safe here.
        _spawn_log_task(_write_log(
            method=request.method,
            path=path,
            status_code=response.status_code,
            response_time_ms=round(elapsed_ms, 2),
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
        ))

        return response
