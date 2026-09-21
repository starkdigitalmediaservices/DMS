import hashlib
import json
import asyncio
import logging
import redis.asyncio as aioredis
from contextlib import asynccontextmanager
from typing import Optional
from app.config import settings
from app.schemas.search import SearchResponse

logger = logging.getLogger(__name__)

_redis_pool = None
# The loop get_redis() itself observed via asyncio.get_running_loop() at
# creation time -- NOT introspected from redis-py internals. The previous
# guard read _redis_pool.connection_pool._loop / _redis_pool._loop, but
# redis.asyncio doesn't reliably expose the pool's bound loop under either
# name across versions, so a stale pool from a closed loop (the backend's
# single long-lived loop is fine, but each Celery task runs its own fresh
# asyncio.run() loop that closes when the task ends, same anti-pattern as
# the DB engine bug fixed in worker.py/config_service.py) slipped past the
# check and raised "Event loop is closed" on first real use -- caught and
# logged as a warning by every caller below, so ingestion never failed, but
# tenant cache invalidation silently no-op'd. Tracking our own loop
# reference removes the dependency on those internals entirely.
_redis_pool_loop = None

async def init_redis():
    global _redis_pool, _redis_pool_loop
    _redis_pool = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    _redis_pool_loop = asyncio.get_running_loop()

async def close_redis():
    global _redis_pool, _redis_pool_loop
    if _redis_pool:
        try:
            await _redis_pool.aclose()
        except Exception:
            pass
        _redis_pool = None
        _redis_pool_loop = None

@asynccontextmanager
async def get_redis():
    global _redis_pool, _redis_pool_loop
    if _redis_pool is not None and _redis_pool_loop is not asyncio.get_running_loop():
        _redis_pool = None
        _redis_pool_loop = None

    if not _redis_pool:
        await init_redis()
    yield _redis_pool

def generate_cache_key(tenant_id: str, query: str, filters: Optional[dict] = None) -> str:
    raw = f"{tenant_id}:{query}:{json.dumps(filters, sort_keys=True)}"
    return f"search:{tenant_id}:{hashlib.sha256(raw.encode()).hexdigest()}"

async def get_cached_search(cache_key: str) -> Optional[SearchResponse]:
    try:
        async with get_redis() as r:
            data = await r.get(cache_key)
            if data:
                try:
                    resp = SearchResponse.model_validate_json(data)
                    resp.cached = True
                    return resp
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Failed to fetch cached search: {e}")
    return None

async def cache_search_result(cache_key: str, result: SearchResponse, ttl: int = 300) -> None:
    try:
        async with get_redis() as r:
            await r.set(cache_key, result.model_dump_json(), ex=ttl)
    except Exception as e:
        logger.warning(f"Failed to set cached search result: {e}")

async def invalidate_tenant_cache(tenant_id: str) -> None:
    try:
        async with get_redis() as r:
            cursor = "0"
            while cursor != 0:
                cursor, keys = await r.scan(cursor=cursor, match=f"search:{tenant_id}:*")
                if keys:
                    await r.delete(*keys)
    except Exception as e:
        logger.warning(f"Tenant cache invalidation notice for {tenant_id}: {e}")


# --- Query-expansion cache --------------------------------------------------
# The trilingual expansion is an LLM round-trip that costs ~1.2s, which is
# ~22% of an uncached search. Unlike the search cache above it is NOT
# tenant-scoped, because the expansion is a pure text transformation of the
# query itself — it reads no tenant data and produces the same English/Hindi/
# Marathi forms whoever asks. Caching it globally means the second person to
# ask a given question skips the round-trip even though their results, their
# filters and their tenant all differ.
#
# TTL is long (24h) because the mapping only changes if the prompt or the
# model changes, neither of which happens between deploys.
_EXPANSION_TTL_SECONDS = 86400


def generate_expansion_cache_key(query: str) -> str:
    return f"qexpand:{hashlib.sha256(query.strip().lower().encode()).hexdigest()}"


async def get_cached_expansion(query: str) -> Optional[dict]:
    try:
        async with get_redis() as r:
            data = await r.get(generate_expansion_cache_key(query))
            if data:
                return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to fetch cached query expansion: {e}")
    return None


async def cache_expansion(query: str, expansion: dict) -> None:
    try:
        async with get_redis() as r:
            await r.set(
                generate_expansion_cache_key(query),
                json.dumps(expansion),
                ex=_EXPANSION_TTL_SECONDS,
            )
    except Exception as e:
        logger.warning(f"Failed to cache query expansion: {e}")


# --- Query-embedding cache --------------------------------------------------
# Embedding the query variants measured ~850ms per search (7 variants through
# BGE-M3 on CPU). Like the expansion above this is a pure function of the text
# — same string in, same vector out, no tenant data involved — so it is cached
# globally and keyed on the text alone.
#
# This is local inference, not a metered API, so the win here is latency and
# CPU rather than quota. Vectors are ~1024 floats; they are stored per variant
# so overlapping variant sets between different queries still hit.
_EMBEDDING_TTL_SECONDS = 86400


async def get_cached_embeddings(texts: list[str]) -> dict[str, list[float]]:
    """Return the subset of `texts` that are already embedded."""
    if not texts:
        return {}
    try:
        async with get_redis() as r:
            keys = [f"qembed:{hashlib.sha256(t.encode()).hexdigest()}" for t in texts]
            values = await r.mget(keys)
            return {
                t: json.loads(v)
                for t, v in zip(texts, values)
                if v
            }
    except Exception as e:
        logger.warning(f"Failed to fetch cached query embeddings: {e}")
        return {}


async def cache_embeddings(text_to_vector: dict[str, list[float]]) -> None:
    if not text_to_vector:
        return
    try:
        async with get_redis() as r:
            pipe = r.pipeline()
            for t, vec in text_to_vector.items():
                pipe.set(
                    f"qembed:{hashlib.sha256(t.encode()).hexdigest()}",
                    json.dumps(vec),
                    ex=_EMBEDDING_TTL_SECONDS,
                )
            await pipe.execute()
    except Exception as e:
        logger.warning(f"Failed to cache query embeddings: {e}")
