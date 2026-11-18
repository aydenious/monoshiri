# MinerU 4.x (ONNX + llama.cpp, CPU): API server. The base `mineru` package runs the
# basic/standard/advanced tiers on CPU with no torch and no CUDA.
#
# --exclude-newer pins every transitive dependency to a cut-off date, so a rebuild later gives
# the same versions.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Model download source. Use huggingface if ModelScope is slow for you.
    MINERU_MODEL_SOURCE=modelscope \
    # All MinerU state (models, config, parse store) under the mounted volume.
    MINERU_HOME=/cache/mineru \
    HF_HOME=/cache/huggingface \
    MODELSCOPE_CACHE=/cache/modelscope \
    MINERU_TELEMETRY_DISABLED=1 \
    MINERU_LLAMA_CPP_BACKEND=cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG MINERU_VERSION=4.1.3
ARG EXCLUDE_NEWER=2026-10-31T00:00:00Z
RUN pip install --upgrade pip uv \
    && uv pip install --system --no-cache --exclude-newer "${EXCLUDE_NEWER}" "mineru==${MINERU_VERSION}"

RUN mkdir -p /cache /app/output
WORKDIR /app
EXPOSE 8000
