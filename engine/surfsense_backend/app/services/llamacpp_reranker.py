"""Monoshiri: reranker backed by a llama.cpp server's ``/v1/rerank`` endpoint.

Replaces the live stack's ``sitecustomize.py`` import hook (RERANKER_SHIM) that
swapped ``rerankers.Reranker`` on import. Selected in ``app/config`` when
``RERANKERS_MODEL_TYPE=llamacpp_api``; URL from ``RERANKER_API_URL``.

Exposes the one method ``RerankerService.rerank_documents`` uses:
``rank(query, docs) -> object with .results``, each result having
``.document`` (the input ``rerankers.Document``), ``.score`` and ``.rank``.
On HTTP failure it raises, and ``RerankerService`` falls back to the
un-reranked order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODEL_TYPE = "llamacpp_api"
DEFAULT_URL = "http://llamacpp-rerank:11438/v1/rerank"
DEFAULT_MODEL = "qwen3-reranker-0.6b"


@dataclass(slots=True)
class LlamacppRerankResult:
    document: Any
    score: float
    rank: int


@dataclass(slots=True)
class LlamacppRankedResults:
    results: list[LlamacppRerankResult]


class LlamacppReranker:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        model: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.url = url
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def rank(self, query: str, docs: list[Any]) -> LlamacppRankedResults:
        if not docs:
            return LlamacppRankedResults(results=[])
        payload = {
            "model": self.model,
            "query": query,
            "documents": [d.text for d in docs],
        }
        try:
            resp = self._client.post(self.url, json=payload)
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            logger.error("[llamacpp_reranker] rerank call failed (url=%s): %s", self.url, e)
            raise
        raw = sorted(
            body.get("results", []),
            key=lambda x: x.get("relevance_score", 0.0),
            reverse=True,
        )
        out: list[LlamacppRerankResult] = []
        for rank_idx, x in enumerate(raw):
            i = x.get("index")
            if i is None or i < 0 or i >= len(docs):
                continue
            out.append(
                LlamacppRerankResult(
                    document=docs[i],
                    score=float(x.get("relevance_score", 0.0)),
                    rank=rank_idx,
                )
            )
        return LlamacppRankedResults(results=out)
