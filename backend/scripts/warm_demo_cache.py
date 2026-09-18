"""Pre-run a list of search queries so they are already cached before a demo.

Why this exists: an uncached search on this corpus takes ~5-25s (embedding +
Cohere rerank + a grounded-answer LLM round-trip), and the Cohere trial key
allows only 10 rerank calls/minute -- so several searches in quick succession
during a live demo both crawl and risk a 429. A successful response is cached
in Redis under (tenant, query, filters) for `search_cache_ttl_seconds`, so
running the intended queries beforehand makes each one return instantly.

The cache key ignores `limit` and `generate_summary`, so warming here matches
what the UI asks for as long as the query text is character-identical and no
search filters are applied.

Usage (inside the backend container):

    docker exec dms-backend-1 python scripts/warm_demo_cache.py \
        --email you@example.com "waqf property" "वक्फ मालमत्ता"

Check `search_cache_ttl_seconds` in /admin/settings first -- warm the cache
within that window of the demo, not the night before.
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from app.database import AppSessionLocal, establish_tenant_context  # noqa: E402
from app.services.auth_service import lookup_user_by_email  # noqa: E402
from app.services.config_service import get_int  # noqa: E402
from app.services.search_service import search  # noqa: E402


async def warm(email: str, queries: list[str], gap: float) -> int:
    async with AppSessionLocal() as db:
        # Goes through the same narrow pre-auth lookup login uses: this
        # connection is the RLS-enforced dms_app role, whose default-deny
        # policy returns nothing until a tenant (or this email) is named.
        user = await lookup_user_by_email(db, email)
        if user is None:
            print(f"No user found for {email!r}", file=sys.stderr)
            return 1

        await establish_tenant_context(db, user.tenant_id)
        ttl = await get_int("search_cache_ttl_seconds", 300)
        print(f"tenant={user.tenant_id}  cache TTL={ttl}s ({ttl // 60} min)\n")

        failures = 0
        for i, q in enumerate(queries, 1):
            started = time.time()
            try:
                resp = await search(
                    query=q, tenant_id=user.tenant_id, user_id=user.id, limit=5,
                    filters=None, db=db, ip_address="127.0.0.1", generate_summary=True,
                )
                took = time.time() - started
                flag = "ok " if resp.results else "EMPTY"
                if not resp.results:
                    failures += 1
                print(f"{i:2}. [{flag}] {took:6.1f}s  mode={resp.search_mode:<22} "
                      f"results={len(resp.results):<3} {q}")
            except Exception as e:
                failures += 1
                print(f"{i:2}. [FAIL]  ----   {q}  :: {type(e).__name__}: {e}")

            # Pace to stay under the reranker's per-minute quota; each search
            # spends up to 2 rerank calls (4 if it falls back to HyDE).
            if i < len(queries):
                await asyncio.sleep(gap)

        print(f"\nwarmed {len(queries) - failures}/{len(queries)} "
              f"(these stay hot for {ttl // 60} min)")
        return 1 if failures else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("queries", nargs="+", help="Exact query strings the demo will type")
    p.add_argument("--email", required=True, help="User whose tenant to warm")
    p.add_argument("--gap", type=float, default=15.0,
                   help="Seconds between queries, to respect the rerank quota (default 15)")
    args = p.parse_args()
    return asyncio.run(warm(args.email, args.queries, args.gap))


if __name__ == "__main__":
    raise SystemExit(main())
