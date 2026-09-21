import cohere
import logging
from typing import List
from cohere.core.request_options import RequestOptions
from app.ai.base import RerankerProvider, EmbeddingProvider, RankedResult

logger = logging.getLogger(__name__)

class CohereEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "embed-english-v3.0"):
        self.api_key = api_key
        self.model = model

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        try:
            client = cohere.AsyncClient(api_key=self.api_key)
            response = await client.embed(
                texts=texts,
                model=self.model,
                input_type="search_document"
            )
            return response.embeddings
        except Exception as e:
            logger.warning("Cohere embed failed or rate limited: %s. Returning zero fallback embeddings.", e)
            return [[0.0] * self.dimensions for _ in texts]

    @property
    def dimensions(self) -> int:
        return 1024

class CohereRerankerProvider(RerankerProvider):
    # Bound how long a single rerank may hold up a search before the caller
    # gives up and degrades to RRF order. Measured, 2026-09-21: on a rate-
    # limited key the SDK's default retry-with-backoff turned one 429 into a
    # 30-34s search (vs ~5s healthy), because nothing capped either the
    # per-attempt timeout or the retry count. Search is user-facing and has
    # a working unranked fallback, so failing fast and degrading beats
    # stalling half a minute to maybe get scores.
    REQUEST_TIMEOUT_SECONDS = 8.0
    MAX_RETRIES = 1

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def rerank(self, query: str, documents: List[str], top_n: int = 5) -> List[RankedResult]:
        if not documents:
            return []
        try:
            client = cohere.AsyncClient(api_key=self.api_key)
            response = await client.rerank(
                model=self.model,
                query=query,
                documents=documents,
                top_n=top_n,
                request_options=RequestOptions(
                    timeout_in_seconds=int(self.REQUEST_TIMEOUT_SECONDS),
                    max_retries=self.MAX_RETRIES,
                ),
            )
            
            results = []
            for r in response.results:
                results.append(RankedResult(
                    index=r.index,
                    score=r.relevance_score,
                    text=documents[r.index]
                ))
            return results
        except Exception as e:
            logger.error("Cohere rerank failed: %s", e)
            raise
