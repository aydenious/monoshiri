# Monoshiri (物知り)

A self-hosted knowledge base you can chat with: upload your books, papers and notes, then ask
questions and get answers that cite the passages they came from.

Monoshiri is a maintained fork of **[SurfSense](https://github.com/MODSetter/SurfSense) 2.1.0**, the
last release of its multi-service, self-hosted line. It is one person's working setup, published as
is. It runs every day on the hardware described below; it is not a product and comes with no support.

## Why this fork exists

SurfSense up to 2.1.0 let you plug in your own **embedding model, reranker and document converter**.
With a strong embedder (Qwen3-Embedding-8B), a cross-encoder reranker and MinerU for PDFs, the
retrieval quality on long, formula-heavy books is very good, and that setup still works like a charm.

Later SurfSense releases removed that. From 2.2.4 the self-hosted stack became a single CPU-only
container: SQLite, one built-in English-only ONNX embedder chosen once, no remote embedders and no
reranker. That fixed architecture caps answer quality, and the old self-hosted line ended upstream on
2026-10-24 with no further fixes.

Monoshiri keeps the 2.1.0 engine, keeps the custom embedder / reranker / converter hooks, removes the
hosted-service parts a personal install never uses, and keeps the dependencies patched.

## The iOS app

**[Monoshiri on the App Store](https://apps.apple.com/app/id6832598586)** (Productivity; live once Apple approves 1.0.0)

Sign in to your own Monoshiri server, browse your library with live conversion progress, add files,
photos or a URL, and chat with streaming answers and citations. The app is a client only: it talks
to the server you point it at and sends your data nowhere else.

## How it fits together

```
                ┌────────────── your reverse proxy (HTTPS) ──────────────┐
 browser / iOS ─┤ /auth/callback*            → frontend   :3000          │
                │ /auth/* /api/* /users*                                  │
                │ /health /verify-token /zero/context → backend :8000     │
                │ /zero/*  (prefix stripped)  → zero-cache :4848          │
                │ everything else             → frontend   :3000          │
                └─────────────────────────────────────────────────────────┘
 backend, celery_worker, celery_beat ── db (Postgres 17 + pgvector), redis, searxng
 backend, celery_worker ── network ai-infra  ── embed-proxy → llamacpp-embed, llamacpp-rerank
 backend, celery_worker ── network mineru-net ── mineru-api (PDF / image / office conversion)
 chat model ── any OpenAI-compatible API (cloud or a local llama.cpp server)
```

Three Docker Compose stacks share two external networks, so the AI services can also serve other apps:

| Stack | Folder | Joins |
|---|---|---|
| Monoshiri | `deploy/compose.yaml` | `default`, `ai-infra`, `mineru-net` |
| AI services (llama.cpp + embed-proxy) | `deploy/ai/` | `ai-infra` |
| MinerU converter | `deploy/mineru/` | `mineru-net` |

### Services and ports

| Service | Image | Container port | Host port (default) | Notes |
|---|---|---|---|---|
| `frontend` | `monoshiri-web` (built from `engine/surfsense_web`) | 3000 | none (via caddy) | Next.js web UI |
| `backend` | `monoshiri-backend` (built from `engine/surfsense_backend`) | 8000 | none (via caddy) | FastAPI, `SERVICE_ROLE=api` |
| `celery_worker` | `monoshiri-backend` | — | — | indexing, conversion, crawling (`SERVICE_ROLE=worker`) |
| `celery_beat` | `monoshiri-backend` | — | — | schedules (`SERVICE_ROLE=beat`) |
| `zero-cache` | `rocicorp/zero:1.6.0` | 4848 | none (via caddy) | live sync for the web UI |
| `db` | `pgvector/pgvector:pg17` | 5432 | none | **needs `wal_level = logical`** (zero-cache) |
| `redis` | `redis:8-alpine` | 6379 | none | Celery broker, invite markers |
| `searxng` | `searxng/searxng:2026.3.13-3c1f68c59` | 8080 | none | web search in chat; needs `json` in `search.formats` |
| `opensandbox-server` | `opensandbox/server:v0.2.2` | 8080 | none | creates the Studio sandbox containers; needs the Docker socket (see below) |
| `llamacpp-embed` | `ghcr.io/ggml-org/llama.cpp:server-<backend>` | 11436 | `127.0.0.1:11436` | Qwen3-Embedding-8B, 4096-d |
| `embed-proxy` | built from `deploy/ai/embed-proxy` | 8000 | `127.0.0.1:11440` | truncates 4096-d → 2000-d (see below) |
| `llamacpp-rerank` | `ghcr.io/ggml-org/llama.cpp:server-<backend>` | 11438 | `127.0.0.1:11438` | Qwen3-Reranker-0.6B, `/v1/rerank` |
| `mineru-api` | built from `deploy/mineru` | 8000 | none | MinerU 4.1.3, bearer key |

Only the reverse proxy should be reachable from outside. The backend trusts the **rightmost**
`X-Forwarded-For` entry for rate limiting, so never publish the backend's port 8000 to untrusted
networks: a client that reaches it directly can forge that header and dodge the login rate limit. The
bundled `caddy` service is the only one with a host port. The llama.cpp and embed-proxy host ports are
bound to loopback, optional (handy for testing from the host); Monoshiri reaches them by name on
`ai-infra`.

**The reverse-proxy path split is required.** The web image is built without a backend URL, so the
browser calls `/api`, `/auth`, `/users` and `/zero` on the page's own origin. Order matters: the more
specific routes first. A ready Caddyfile is in `deploy/Caddyfile`.

## Reference hardware

The author's box, for scale:

- Mini PC, **AMD Ryzen AI 9 HX PRO 370** (12 cores / 24 threads), **Radeon 890M** iGPU
  (RDNA 3.5, `gfx1150`), **60 GB RAM**, Debian 13, kernel 6.12.
- llama.cpp runs on the iGPU through **ROCm** (`server-rocm` image). The iGPU has no VRAM of its own;
  models live in GTT (system RAM the GPU can map), about 30 GB on this machine.
- MinerU runs on the **CPU** (12 threads, standard tier, concurrency 1). A 301-page formula-heavy
  textbook converts in about 73 minutes; a photo or short PDF in about a minute.
- The chat model is a cloud API (DeepSeek, Qwen via DashScope). Any OpenAI-compatible endpoint works,
  including a local llama.cpp server.

Measured memory on that box, models resident:

| Service | Model file | On disk | Context × slots |
|---|---|---|---|
| `llamacpp-embed` | `Qwen3-Embedding-8B-Q4_K_M.gguf` | 4.7 GB | 16384 total, 2 slots (8192 each) |
| `llamacpp-rerank` | `qwen3-reranker-0.6b-q8_0.gguf` | 0.6 GB | 16384 total, 4 slots |
| `mineru-api` | MinerU standard-tier models | 2 GB | container memory limit 14 GB |

### AI services compose (AMD ROCm, as run)

`deploy/ai/compose.yaml` is the author's file, with the models path made a variable (`MODELS_DIR`, default `./models`):

```yaml
# AI services for Monoshiri: llama.cpp embedder and reranker, plus the embed-proxy.
# Joins the external network ai-infra (docker network create ai-infra).
# Put the two GGUF files in ${MODELS_DIR:-./models}.
# The host ports below are bound to loopback and are only for testing from this host
# (curl http://127.0.0.1:11436/...). Monoshiri reaches the services by name on ai-infra, so
# you can remove them.
services:
  llamacpp-embed:
    image: ghcr.io/ggml-org/llama.cpp:server-rocm
    devices: [/dev/kfd, /dev/dri]
    group_add: ["992"]          # the host's `render` group id: getent group render
    volumes: ["${MODELS_DIR:-./models}:/models:ro"]
    command: [--host, 0.0.0.0, --port, "11436",
              --model, /models/Qwen3-Embedding-8B-Q4_K_M.gguf, --alias, qwen3-embedding-8b,
              --n-gpu-layers, "999", --embeddings,
              --ctx-size, "16384", --parallel, "2", --cont-batching,
              --cache-ram, "0"]
    ports: ["127.0.0.1:11436:11436"]
    networks: [ai-infra]
    restart: unless-stopped
  llamacpp-rerank:
    image: ghcr.io/ggml-org/llama.cpp:server-rocm
    devices: [/dev/kfd, /dev/dri]
    group_add: ["992"]          # the host's `render` group id: getent group render
    volumes: ["${MODELS_DIR:-./models}:/models:ro"]
    command: [--host, 0.0.0.0, --port, "11438",
              --model, /models/qwen3-reranker-0.6b-q8_0.gguf, --alias, qwen3-reranker-0.6b,
              --n-gpu-layers, "999", --reranking,
              --ctx-size, "16384", --parallel, "4", --cont-batching,
              --cache-ram, "512"]
    ports: ["127.0.0.1:11438:11438"]
    networks: [ai-infra]
    restart: unless-stopped
  embed-proxy:
    build: ./embed-proxy
    environment: { UPSTREAM_BASE: "http://llamacpp-embed:11436", TARGET_DIM: "2000",
                   MAX_INPUT_CHARS: "8000", UPSTREAM_TIMEOUT: "1800" }
    ports: ["127.0.0.1:11440:8000"]
    networks: [ai-infra]
    restart: unless-stopped
networks:
  ai-infra: { external: true }
```

Lessons behind those flags:

- **Per-slot context must stay ≥ 8192 for the embedder.** The chunker sizes chunks with a different
  tokenizer than Qwen's, so a chunk can arrive a bit over its nominal size. Per-slot context is
  `--ctx-size ÷ --parallel`. Lower `--parallel` to save memory, not the per-slot size.
- **`--cache-ram`.** llama-server keeps a host-RAM prompt cache of 8 GB per server by default. Unique
  documents never reuse it, so set it to 0 (embed) or small (rerank); on the reference box this freed
  about 14 GB of RAM.
- **Out-of-memory on an iGPU** shows up as `ROCm error: out of memory` during decode, not at load. Cut
  total context or slots, and don't keep models resident that nothing uses.
- **Why the embed-proxy.** pgvector's HNSW index supports at most 2000 dimensions for `vector`.
  Qwen3-Embedding is trained with Matryoshka representation learning (MRL), so its first 2000 of 4096
  dimensions, re-normalised, are still a good embedding. The proxy truncates and re-normalises
  between the backend and llama.cpp. With an embedder of 2000 dimensions or fewer, skip the proxy and
  point `EMBEDDING_API_BASE` straight at llama.cpp.

### Choosing for your hardware

Pick the llama.cpp image for your GPU; everything else stays the same.

| Hardware | Image tag | Compose device setup | Notes |
|---|---|---|---|
| **AMD GPU / APU, ROCm** (what the author runs) | `server-rocm` | `devices: [/dev/kfd, /dev/dri]`, `group_add` = host `render` (and on some distros `video`) group id | Best speed on supported AMD parts. Consumer cards outside ROCm's official list may need `HSA_OVERRIDE_GFX_VERSION` (for example `11.0.0` for RDNA 3). On an APU the model memory is GTT, so check `/sys/class/drm/card*/device/mem_info_gtt_total` before sizing models. |
| **AMD or Intel GPU, Vulkan** | `server-vulkan` | `devices: [/dev/dri]` | No ROCm needed, works on far more GPUs. Usually a little slower than ROCm; a good first try, and the fallback if ROCm is unstable on your chip. |
| **NVIDIA GPU, CUDA** | `server-cuda` | install the NVIDIA Container Toolkit, then `deploy: {resources: {reservations: {devices: [{driver: nvidia, count: all, capabilities: [gpu]}]}}}`; no `/dev/kfd`, no `group_add` | The most mature backend. Model memory is the card's VRAM: the 8B embedder (Q4) plus the reranker fits in about 8 GB with the contexts above. |
| **CPU only** | `server` | none; drop `--n-gpu-layers` | Works, but indexing a large library with the 8B embedder is slow. Use a small embedder (below). |
| **Apple Silicon Mac** | none in Docker | Docker on macOS can't reach the GPU. Run `llama-server` natively (Homebrew `llama.cpp`, Metal) and set the URLs to `http://host.docker.internal:<port>` | Unified memory makes the 8B embedder comfortable on 16 GB or more. |

**Less memory?** Use `Qwen3-Embedding-0.6B` (1024 dimensions, about 1 GB): no embed-proxy needed,
set `EMBEDDING_DIMENSION=1024`. Quality on hard questions drops, but it runs anywhere.

**Choose the embedder before you index.** The database's vector columns take the dimension of the
first start, and the backend refuses to start when `EMBEDDING_DIMENSION` doesn't match them. Changing
embedder later means a fresh database and re-uploading your documents.

**AMD stability note.** On the `gfx1150` iGPU the author hit rare full-host hangs (`MES failed to
respond`) under GPU compute load; booting with `amdgpu.cwsr_enable=0` stopped them. If ROCm is
unstable on your chip, try the Vulkan image.

## Quick start

> Tested on a fresh, empty database on 2026-11-18.

1. **Networks:** `docker network create ai-infra` and `docker network create --internal mineru-net`.
2. **AI services:** download the two GGUF files into your models folder, adjust `deploy/ai/compose.yaml`
   for your hardware, `docker compose up -d` there.
3. **MinerU (optional, recommended for PDFs):** set `MINERU_API_KEY` in `deploy/mineru/.env`, then
   `docker compose up -d --build` there. Without it, set `ETL_SERVICE=DOCLING` (built-in, slower,
   weaker on formulas).
4. **Build the images** (no prebuilt images are published):
   `docker build --target production -t monoshiri-backend:local engine/surfsense_backend` and
   `docker build -t monoshiri-web:local engine/surfsense_web`.
5. **Configure:** in `deploy/`:

   ```sh
   cp .env.example .env
   cp global_llm_config.example.yaml global_llm_config.yaml
   for v in SECRET_KEY SEARXNG_SECRET ZERO_ADMIN_PASSWORD DB_PASSWORD; do
     sed -i "s/^$v=.*/$v=$(openssl rand -hex 32)/" .env
   done
   ```

   Then add your chat model's API key to `global_llm_config.yaml`. Chat's default model is Auto,
   which picks among the entries in that file (DeepSeek included), so if you only run a local
   model, delete the DeepSeek entry. A local chat model needs at least 16k tokens of context per
   slot: the agent's first request is about 12k. Do the copy before the first
   start: if `global_llm_config.yaml` is missing, Docker creates a directory of that name at the
   bind mount and the backend fails to read its config.
6. **Start:** `docker compose up -d` in `deploy/`. The bundled `caddy` service (`deploy/Caddyfile`)
   serves on port 8080 (`HTTP_PORT`); put HTTPS in front of it.
7. **First account:** sign-up is invite-only.
   `docker compose exec backend python -m app.monoshiri invite --note "me"` prints a one-time link.

Operational notes:

- The schema is created on first start (`create_all`). **Don't run Alembic** against a Monoshiri
  database: the fork's tables were stripped, and the upstream migration chain no longer matches.
- After recreating `backend` or `frontend`, run `docker compose restart zero-cache`, or the
  Documents panel goes blank (zero-cache keeps old container IPs).

## Code sandbox (Studio outputs)

Mind maps, flashcards, quizzes, slide decks and PDF/DOCX reports are rendered in a short-lived
sandbox container. The `opensandbox-server` service creates those containers on the host Docker
daemon (it mounts `/var/run/docker.sock`) and the backend reaches it by name on the compose network;
the server itself publishes no host port.

Each sandbox takes 1 CPU and 2 GiB. A workspace may have `SANDBOX_MAX_SESSIONS_PER_WORKSPACE` (2)
alive at once, and a chat keeps its sandbox until `SANDBOX_IDLE_TTL_SECONDS` (300) pass with no use.
A third output started inside that window fails with "Sandbox limit reached for this workspace";
wait a few minutes, or raise the cap if the host has the memory.

The sandbox ships turned off (`SANDBOX_ENABLED=FALSE`). Add the firewall rule below first, then
build the image and set `SANDBOX_ENABLED=TRUE`. Build the sandbox image once, and keep its tag in
`deploy/.env` as `SANDBOX_IMAGE`:

```sh
docker build -t monoshiri-sandbox:local engine/docker/sandbox
```

Turning the feature off is `SANDBOX_ENABLED=FALSE` in `deploy/.env`, then
`docker compose up -d backend celery_worker`.

**Firewall the sandbox ports.** Sandbox containers publish their own (unauthenticated) port on the
Docker host in **20000-30000**. Drop new inbound TCP to that range from the LAN and from any VPN or
public interface, in Docker's `DOCKER-USER` chain (rules in `INPUT` sit behind Docker's own):

```sh
iptables -I DOCKER-USER -i <iface> -p tcp -m conntrack --ctorigdstport 20000:30000 --ctdir ORIGINAL -j DROP
```

Check it is there with `iptables -S DOCKER-USER`. The rule does not survive a reboot on its own, so
persist it (for example a oneshot systemd unit ordered after `docker.service`). The backend does not
need those ports: it talks to the sandbox through the server proxy.

## What was removed from SurfSense 2.1.0

- Stripe payments, billing, credits, premium quotas and incentive tasks
- Sunset mode
- All 30+ third-party connectors (Notion, Slack, Google Drive, GitHub and the rest) and Composio
- Chat gateways: Slack, Discord, WhatsApp, Telegram
- Anonymous chat and public chat sharing
- Team management: roles, members, workspace invites
- PostHog analytics
- The Playground and the scraper routes and sub-agents (Google Maps and similar)
- The marketing homepage (`/` now goes to the login page)
- The Electron desktop app and the desktop-only backend routes, auth endpoints and agent filesystem mode
- The "Add YouTube videos" form that had no backend behind it (saving a YouTube URL works again, see below)
- Model mappings and tables for hosted services the app no longer uses
- Dead code: unreachable modules, unused web files and npm packages, duplicated logic

## What was added

- Invite-only sign-up with signed, one-time links, plus a login rate limit
- An admin Users page
- MinerU conversion for PDFs, images and office files, in 10-page batches with progress and
  resume after a restart
- Save a URL: an article, a PDF link or a YouTube transcript, fetched with SSRF protection
- A llama.cpp reranker client (`RERANKERS_MODEL_TYPE=llamacpp_api`)
- A pinned embedding dimension: the backend refuses to start on a mismatch instead of guessing
- A frozen API contract (`docs/contracts/openapi.json`), token login and refresh for native apps, and
  a generated Swift client (`clients/swift`)
- Chat turns that keep running when the client disconnects, and per-reply budgets for web searches
  and crawled pages
- Errors that used to be silently swallowed are now caught narrowly and logged
- Dependency and CVE updates (Next.js 16.4, LiteLLM, Docling, OpenTelemetry and others)

## Licence

Monoshiri keeps SurfSense's licences:

- `engine/surfsense_backend/` — Apache-2.0 (`engine/LICENSE`)
- `engine/surfsense_web/` — MIT (`engine/surfsense_web/LICENSE`)
- `engine/surfsense_backend/app/proprietary/` — **Business Source License 1.1**, source-available, not
  open source (the web crawler and the platform scrapers, `app/proprietary/LICENSE`): you may run it for
  yourself, but not offer it as a hosted or commercial service to others. Each release
  converts to Apache-2.0 four years after it was published.

Original work © the SurfSense authors. Fork changes © the Monoshiri author, under the same licences.
