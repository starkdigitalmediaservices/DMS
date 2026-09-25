import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy import event, text
from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.postgres_url,
    echo=settings.app_env == 'development',
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# D-2 security review, 2026-09-07 — `engine`/`AsyncSessionLocal` above
# connect as `docsearch`, a genuine Postgres superuser (POSTGRES_USER in
# docker-compose.yml), and superusers unconditionally bypass Row-Level
# Security regardless of how carefully a policy is written — confirmed
# live: `FORCE ROW LEVEL SECURITY` on 17 tenant-scoped tables was doing
# nothing (docs/decisions/D2_tenant_isolation_security_review.md, Finding 1).
#
# `app_engine`/`AppSessionLocal` is a SEPARATE connection pool authenticating
# as the restricted `dms_app` role instead (NOSUPERUSER NOBYPASSRLS,
# created by migration 0046) — this is what get_db/get_tenant_db below
# actually use, so every real FastAPI request goes through a connection RLS
# can actually enforce, not one that ignores it. Deliberately NOT used for
# the test suite, Alembic migrations, or worker.py's background tasks
# (ingestion pipeline, the cross-tenant trash-retention sweep, the
# folder/SFTP/email connectors) — those already have a different, audited
# trust boundary (see the review doc) and some of them (the retention
# sweep) legitimately need the broader access a single HTTP request never
# should have.
if settings.app_postgres_url:
    app_engine = create_async_engine(
        settings.app_postgres_url,
        echo=settings.app_env == 'development',
        pool_size=10,
        max_overflow=20,
    )
else:
    logger.warning(
        "APP_POSTGRES_URL is not set -- FastAPI requests are falling back to the "
        "same superuser connection as everything else, so Row-Level Security "
        "provides no real protection (see docs/decisions/D2_tenant_isolation_security_review.md). "
        "Set APP_POSTGRES_URL to the restricted `dms_app` role from migration 0046 "
        "to close this gap."
    )
    app_engine = engine

class _AppSession(Session):
    """Sync session class behind AppSessionLocal; exists so the
    after_begin listener below only ever touches request connections."""


# Postgres settings a request's RLS policies read (tenant, caller, and
# department scope -- see deps.get_tenant_db). set_config(..., false) lives
# on the physical connection, and a mid-request db.commit() can hand the
# session a different pooled connection that has none of them (the T96
# "Could not refresh instance" bug establish_tenant_context works around
# call site by call site). Keeping the values on the session and replaying
# them whenever a transaction begins makes every connection the session
# touches carry the same context, which matters more now that losing
# app.dept_scoped would silently widen what a scoped user can see.
REQUEST_GUCS_KEY = "request_gucs"


def _set_config_statement(gucs: dict):
    items = list(gucs.items())
    sql = "SELECT " + ", ".join(f"set_config(:n{i}, :v{i}, false)" for i in range(len(items)))
    params = {}
    for i, (name, value) in enumerate(items):
        params[f"n{i}"], params[f"v{i}"] = name, value
    return text(sql), params


@event.listens_for(_AppSession, "after_begin")
def _replay_request_gucs(session, transaction, connection):
    gucs = session.info.get(REQUEST_GUCS_KEY)
    if gucs:
        connection.execute(*_set_config_statement(gucs))


async def set_request_gucs(session: AsyncSession, gucs: dict) -> None:
    """Apply these settings now and on every later transaction of this session."""
    session.info.setdefault(REQUEST_GUCS_KEY, {}).update(gucs)
    await session.execute(*_set_config_statement(gucs))


AppSessionLocal = async_sessionmaker(
    app_engine,
    class_=AsyncSession,
    sync_session_class=_AppSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

class Base(DeclarativeBase):
    pass

async def establish_tenant_context(session: AsyncSession, tenant_id) -> None:
    """D-2 fix, relocated here 2026-09-07 (T96 clean-room finding) — was
    only defined in auth_service.py, but the same re-establish-after-commit
    need turned out to be systemic, not auth-specific (see below), so this
    lives next to _reset_session_tenant_context instead of being duplicated
    or cross-imported from an unrelated service module.

    Call this the moment a request first learns which tenant it's acting
    for (or again, any time a mid-function `db.commit()` is followed by
    more tenant-scoped work on the same session) — session-scoped
    (is_local=false) so it's meant to survive an in-request commit, and
    _reset_session_tenant_context (called on every get_db()/get_tenant_db()
    exit path) is what keeps that safe on a pooled connection afterward.

    Real bug found live 2026-09-07: a mid-function `db.commit()` followed
    by `db.refresh(row)` reproducibly 500'd with "Could not refresh
    instance" on sign_up() AND document_service.toggle_star_document() —
    two unrelated functions on two different get_db()/get_tenant_db()
    sessions, same shape (commit, then a further SELECT under
    dms_app's default-deny RLS with no tenant context visible to it). Not
    a hypothetical race: reproduced deterministically, standalone, no
    concurrent request in flight. Whatever the exact connection-pool
    mechanism is, re-establishing context immediately after any commit
    that's followed by more tenant-scoped reads is the safe, no-downside
    fix — applied here to every function with this shape
    (document_service.upload_document/update_document/
    toggle_star_document/toggle_trash_document,
    folder_service.create_folder/update_folder/toggle_star_folder/
    toggle_trash_folder, auth_service.sign_up)."""
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": str(tenant_id)}
    )

async def _reset_session_tenant_context(session: AsyncSession) -> None:
    """D-2 fix — the session-scoped `set_config(..., false)` used below (see
    get_tenant_db, establish_tenant_context, lookup_user_by_email) survives
    an in-request `db.commit()`, which real service code does mid-function
    more than once (upload_document, sign_up — found live: both broke
    under `is_local=true`, the transaction-scoped alternative, the instant
    something after their own commit needed the context again). The
    tradeoff is this MUST be reset before the underlying connection goes
    back to the pool, or one request's tenant leaks into the next unrelated
    one that happens to reuse it -- every get_db()/get_tenant_db() exit
    path calls this, regardless of whether this particular request ever
    set anything, so cleanup is centralized instead of dependent on every
    caller remembering to."""
    session.info.pop(REQUEST_GUCS_KEY, None)
    try:
        await session.execute(text(
            "SELECT set_config('app.current_tenant_id', '', false), "
            "set_config('app.login_lookup_email', '', false), "
            "set_config('app.current_user_id', '', false), "
            "set_config('app.dept_scoped', '', false), "
            "set_config('app.scope_folder_ids', '', false)"
        ))
        await session.commit()
    except Exception:
        # Best-effort: a connection that can't even run a RESET is broken
        # and won't be reused by the pool anyway, so there's no leak risk.
        pass

async def get_db():
    async with AppSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await _reset_session_tenant_context(session)
            await session.close()

async def get_db_with_tenant(tenant_id: str | None = None):
    async with AppSessionLocal() as session:
        try:
            if tenant_id:
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :t, false)"),
                    {"t": str(tenant_id)}
                )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await _reset_session_tenant_context(session)
            await session.close()
