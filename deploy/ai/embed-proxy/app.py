"""MRL embedding-truncation proxy.

Sits in front of an OpenAI-compatible embedding server (llama.cpp) and returns
Matryoshka-truncated, L2-renormalized vectors of TARGET_DIM dimensions.

Purpose: let vanilla SurfSense (which hard-caps embeddings at 2000 dims and
stores plain Vector(dim)) keep using the 4096-dim Qwen3-Embedding-8B model
WITHOUT any SurfSense code patches. The dimension reduction lives here, in
infra, where upstream upgrades can never break it.

Qwen3-Embedding is trained Matryoshka-style, so keeping the first N dimensions
and renormalizing is the intended truncation.
"""

from __future__ import annotations

import math
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "http://llamacpp-embed:11436")
TARGET_DIM = int(os.environ.get("TARGET_DIM", "2000"))
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "300"))
# Cap per-input length before forwarding. SurfSense embeds a document-level
# vector over the WHOLE (truncated-to-8192-cl100k-token) markdown; on this iGPU
# a ~9k-token embedding takes >120s and, combined with litellm retries, stampedes
# the embed slots. The document vector is a coarse signal (chunk vectors carry
# the detail), so bounding its input keeps ingestion fast. 0 disables. Chunks
# (<=512 tokens ~2KB) are well under this and pass through untouched.
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "0"))

app = FastAPI(title="mrl-embed-proxy")


def _clip(inp):
    """Truncate string inputs to MAX_INPUT_CHARS; leave token-id inputs alone."""
    if MAX_INPUT_CHARS <= 0 or inp is None:
        return inp
    if isinstance(inp, str):
        return inp[:MAX_INPUT_CHARS]
    if isinstance(inp, list):
        return [s[:MAX_INPUT_CHARS] if isinstance(s, str) else s for s in inp]
    return inp


def truncate_normalize(vec: list[float], dim: int) -> list[float]:
    """Keep the first ``dim`` values and L2-renormalize to a unit vector."""
    v = vec[:dim]
    norm = math.sqrt(sum(x * x for x in v))
    if norm > 0.0:
        v = [x / norm for x in v]
    return v


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "target_dim": TARGET_DIM, "upstream": UPSTREAM_BASE}


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    body = await request.json()
    # Upstream ignores the OpenAI `dimensions` param; we own truncation, so drop it.
    body.pop("dimensions", None)
    if "input" in body:
        body["input"] = _clip(body["input"])
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{UPSTREAM_BASE}/v1/embeddings", json=body)
    data = r.json()
    if r.status_code == 200 and isinstance(data.get("data"), list):
        for item in data["data"]:
            emb = item.get("embedding")
            if isinstance(emb, list):
                item["embedding"] = truncate_normalize(emb, TARGET_DIM)
    return JSONResponse(data, status_code=r.status_code)


@app.get("/v1/models")
async def models() -> JSONResponse:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{UPSTREAM_BASE}/v1/models")
    return JSONResponse(r.json(), status_code=r.status_code)
