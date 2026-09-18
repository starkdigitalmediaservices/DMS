import asyncio
import logging
import re
from openai import AsyncOpenAI
from typing import List, Optional, Union
from app.ai.base import LLMProvider, Message

logger = logging.getLogger(__name__)

# 2026-09-15 — Groq caps each key at 8000 tokens/minute
# (x-ratelimit-limit-tokens), and a single grounding-answer call alone can
# request max_tokens=2500, so a genuine quota exhaustion IS possible under
# heavy sustained traffic -- this backoff reads Groq's own reset estimate
# for that real case. NOTE this is NOT confirmed to be what causes the
# backend test suite's own "empty string, no headers at all" failures
# (see search_service.py's _expand_trilingual_query docstring): a fresh,
# separate process using the same keys succeeded instantly, with a
# healthy 7923/8000 tokens remaining, seconds after the suite's own calls
# had failed 40 times in a row -- ruling out real Groq/Cloudflare
# throttling for that specific failure mode. That one looks like
# something internal to the test process itself (event-loop/connection
# contention from mixing heavy synchronous work with async Groq calls in
# one process), not a quota problem this backoff can fix. Kept anyway
# because it's still correct behavior for an actual rate-limit response.
MAX_BACKOFF_SECONDS = 8.0
DEFAULT_BACKOFF_SECONDS = 1.0

_DURATION_MS_RE = re.compile(r"^(\d+(?:\.\d+)?)ms$")
_DURATION_MS_RE_ALT = re.compile(r"^(?:(\d+)m)?(\d+(?:\.\d+)?)s$")


def _parse_groq_duration(raw: Optional[str]) -> Optional[float]:
    """Parse Groq's rate-limit reset-duration headers (e.g. "577ms",
    "2.5s", "31m40.8s") into seconds. Returns None for anything that
    doesn't match a known shape rather than raising -- this feeds a
    best-effort backoff, not something worth crashing over."""
    if not raw:
        return None
    m = _DURATION_MS_RE.match(raw)
    if m:
        return float(m.group(1)) / 1000
    m = _DURATION_MS_RE_ALT.match(raw)
    if m:
        minutes = int(m.group(1)) if m.group(1) else 0
        return minutes * 60 + float(m.group(2))
    return None


class GroqLLMProvider(LLMProvider):
    def __init__(self, api_key: Union[str, List[str]], model: str):
        if isinstance(api_key, str):
            self.api_keys = [k.strip() for k in api_key.split(",") if k.strip()]
        else:
            self.api_keys = [k.strip() for k in api_key if k and k.strip()]

        if not self.api_keys:
            raise ValueError("No valid Groq API keys provided.")

        self.model = model
        self._current_index = 0

    async def complete(self, messages: List[Message], temperature: float = 0.1, max_tokens: int = 1024) -> str:
        formatted = [{"role": m.role, "content": m.content} for m in messages]
        attempts = 0
        max_attempts = len(self.api_keys)
        last_exception = None

        while attempts < max_attempts:
            current_key = self.api_keys[self._current_index]
            wait_seconds = DEFAULT_BACKOFF_SECONDS
            try:
                client = AsyncOpenAI(
                    api_key=current_key,
                    base_url="https://api.groq.com/openai/v1"
                )
                raw_response = await client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=formatted, # type: ignore
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                response = raw_response.parse()
                if not hasattr(response, "choices") or not response.choices:
                    # Seen live, 2026-09-15: the SDK call returns cleanly
                    # (no exception) but `response` isn't a real
                    # ChatCompletion -- this used to surface as a generic,
                    # hard-to-diagnose "'str' object has no attribute
                    # 'choices'" AttributeError. It happened every time
                    # alongside a near-zero x-ratelimit-remaining-tokens on
                    # this same response, so treat it as quota exhaustion
                    # and back off by the header's own reset estimate
                    # instead of hammering the same empty bucket.
                    reset = raw_response.headers.get("x-ratelimit-reset-tokens") or raw_response.headers.get("x-ratelimit-reset-requests")
                    wait_seconds = min(_parse_groq_duration(reset) or DEFAULT_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS)
                    raise RuntimeError(
                        f"Groq returned a response with no choices (type {type(response).__name__}) -- "
                        f"remaining tokens: {raw_response.headers.get('x-ratelimit-remaining-tokens')}, "
                        f"reset in: {reset}"
                    )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_exception = e
                headers = getattr(getattr(e, "response", None), "headers", None)
                if headers is not None:
                    reset = headers.get("retry-after") or headers.get("x-ratelimit-reset-tokens") or headers.get("x-ratelimit-reset-requests")
                    parsed = _parse_groq_duration(reset)
                    if parsed is None and reset is not None:
                        try:
                            parsed = float(reset)  # retry-after is sometimes plain seconds, not Groq's duration shape
                        except ValueError:
                            parsed = None
                    if parsed is not None:
                        wait_seconds = min(parsed, MAX_BACKOFF_SECONDS)
                logger.warning(
                    f"Groq API Key (index {self._current_index}, key ending ...{current_key[-6:]}) failed: {e}. "
                    f"Waiting {wait_seconds:.1f}s before rotating..."
                )
                await asyncio.sleep(wait_seconds)
                self._current_index = (self._current_index + 1) % len(self.api_keys)
                attempts += 1

        logger.error(f"All {max_attempts} Groq API key(s) failed or hit rate limits.")
        if last_exception:
            raise last_exception
        raise RuntimeError("All Groq API keys failed.")
