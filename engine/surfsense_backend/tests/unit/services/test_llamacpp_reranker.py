"""Monoshiri: LlamacppReranker against a fake llama.cpp /v1/rerank."""

import json

import httpx
import pytest
from rerankers import Document

from app.services.llamacpp_reranker import LlamacppReranker
from app.services.reranker_service import RerankerService

pytestmark = pytest.mark.unit


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rank_orders_by_score_and_maps_back_to_documents():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.5},
                    {"index": 7, "relevance_score": 0.99},  # out of range: dropped
                ]
            },
        )

    docs = [Document(text=t, doc_id=i) for i, t in enumerate(["a", "b", "c"])]
    r = LlamacppReranker(url="http://x/v1/rerank", model="m", client=_client(handler))

    out = r.rank("q", docs).results

    assert seen["body"] == {"model": "m", "query": "q", "documents": ["a", "b", "c"]}
    assert [x.document.text for x in out] == ["c", "b", "a"]
    assert [x.rank for x in out] == [1, 2, 3]  # rank index is post-sort, out-of-range at 0
    assert out[0].score == pytest.approx(0.9)


def test_empty_docs_makes_no_request():
    def handler(request):
        raise AssertionError("no HTTP call expected")

    r = LlamacppReranker(client=_client(handler))
    assert r.rank("q", []).results == []


def test_service_falls_back_to_original_order_on_http_error():
    r = LlamacppReranker(client=_client(lambda req: httpx.Response(500)))
    docs = [
        {"document_id": 1, "content": "a", "score": 0.2, "document": {"id": 1}},
        {"document_id": 2, "content": "b", "score": 0.1, "document": {"id": 2}},
    ]
    assert RerankerService(r).rerank_documents("q", docs) == docs
