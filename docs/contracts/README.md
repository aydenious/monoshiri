# Monoshiri API contract

`openapi.json` in this directory is the frozen contract for the Monoshiri HTTP API. It is generated
from the backend (`scripts/export-openapi.py`), committed, and checked in CI: if the app's OpenAPI
schema differs from the committed file, the build fails. A Swift client is generated from the same
file (`clients/swift/`).

Regenerate after any intentional API change, in the same pull request:

```sh
cd engine/surfsense_backend && uv run python ../../scripts/export-openapi.py
```

The exporter pins the environment it imports the app under (LOCAL auth, dummy database, the same
embedding model as CI), so output does not depend on a local `.env` or `SECRET_KEY`. Two runs
produce byte-identical files.

## Base URL

```
https://monoshiri.example.com
```

All REST routes below are relative to it. The versioned API prefix is `/api/v1`; auth and user
routes are unprefixed (`/auth/...`, `/users/me`).

## Authentication (native clients)

1. `POST /auth/jwt/token` — `application/x-www-form-urlencoded` with `username` (email) and
   `password` (OAuth2 password form). Response body:

   ```json
   {"access_token": "...", "refresh_token": "...", "token_type": "bearer", "access_expires_at": 1799999999}
   ```

   No cookies are set. Bad credentials or an inactive account → `400 {"detail": "LOGIN_BAD_CREDENTIALS"}`.
   The route shares the login rate limit (5 attempts/minute/IP) and the registration kill switch with
   the web login.
2. Send `Authorization: Bearer <access_token>` on every request.
3. `POST /auth/jwt/refresh` with JSON `{"refresh_token": "..."}` → a new pair in the body. Refresh
   tokens rotate on every use; the old token is revoked. During the 45 s access-only grace window
   after a rotation, refreshing again with the old token returns `refresh_token: null` — keep the
   current refresh token.
4. `POST /auth/jwt/revoke` with JSON `{"refresh_token": "..."}` on logout.

Lifetimes: **access 1 h**, **refresh 14 d** sliding, **30 d absolute** cap. Tokens belong in the
Keychain. The web login (`POST /auth/jwt/login`) is unchanged: it still answers with session cookies.

## Document status semantics

Documents carry `status` (the same JSONB the web UI reads over Zero). The four `state` values are
fixed — the web UI depends on them:

| field | meaning |
|---|---|
| `state` | exactly `pending` \| `processing` \| `ready` \| `failed` |
| `reason` | failure text when `state` is `failed` |
| `stage` | current processing step, e.g. `converting`, `waiting_for_converter`, `indexing` |
| `pages_done` / `pages_total` | conversion progress; `pages_total` is `null` for non-paged inputs (photos, URL) |
| `updated_at` | ISO 8601 time of the last status refresh written by the worker |
| `health` | **computed by the API, never stored**: `working` \| `stalled` \| `waiting` |

- **Waiting** = the converter (MinerU) is unreachable; `stage` is `waiting_for_converter` and the
  worker keeps retrying with backoff → `health: "waiting"`. A `waiting` row that has not refreshed
  `updated_at` for **25 minutes** (`WAITING_STALLED_AFTER`) is no longer a live wait and reads as
  `stalled`.
- **Stalled** = no progress for **15 minutes** (`STALLED_AFTER`) on a `pending` or `processing` row
  — e.g. the worker or its host is down → `health: "stalled"`. The client must not compute this
  itself.
- Status is returned by the existing document routes (new fields added); there is no separate status
  route other than the batch poller `GET /api/v1/documents/status`.


## Upload

`POST /api/v1/documents/fileupload` — `multipart/form-data`:

- `files` (one or more file parts; PDF, images incl. HEIC, DOCX/PPTX)
- `workspace_id` (form field, required)
- `use_vision_llm`, `processing_mode` (optional)

Returns immediately; each file is a document with `state: "pending"` that becomes `processing` and
then `ready` (or `failed`, with `reason`). Poll `GET /api/v1/documents/status` or the single-document
route for progress.

## Save a URL

`POST /api/v1/documents/url` — JSON `{"workspace_id": ..., "url": ..., "folder_id": null}`
(`folder_id` optional). The page's readable content, a YouTube transcript, or a linked PDF (converted
like an upload, with page progress) is saved as a document in the workspace, with the same
status/progress model.

- `202 {"document_id": n}` — a new row (or a failed/stalled row being retried) was queued.
- `200 {"document_id": n}` — the URL is already saved or being saved; nothing new is queued.
- `400 {"detail": "..."}` (`UrlRejected`) — the server refuses the address: not a valid URL, not
  http/https, or the host is not publicly reachable (private/LAN addresses are refused before any
  fetch; a redirect to one is caught in the worker and fails the row with "URL is not publicly
  reachable"). `detail` is a fixed sentence, safe to show.
- `422` — the request body itself is malformed (missing field, wrong type).

## Chat stream (SSE)

Chat is not modelled by the OpenAPI generator: `POST /api/v1/new_chat` (also
`POST /api/v1/threads/{thread_id}/regenerate` and `.../resume`) returns
`text/event-stream` speaking the **Vercel AI UI message stream v1** protocol, announced by the
header `x-vercel-ai-ui-message-stream: v1`. Each event is one JSON object on a `data: ` line, blank
line separated; the stream ends with `data: [DONE]`.

Event `type` values the backend emits:

- lifecycle: `start`, `start-step`, `finish-step`, `finish`
- text: `text-start`, `text-delta`, `text-end`
- reasoning: `reasoning-start`, `reasoning-delta`, `reasoning-end`
- tools: `tool-input-start`, `tool-input-delta`, `tool-input-available`, `tool-output-available`
- sources/files: `source-url`, `source-document`, `file`
- `error`
- data parts: `data-activities`, `data-revert-results`, `data-thread-title-update`, `data-weather`,
  `data-further-questions`, `data-activity`, `data-activity-timing`, `data-turn-info`,
  `data-turn-status`, `data-interrupt-request`, `data-action-log`, `data-action-log-updated`,
  `data-subagent-start`, `data-subagent-finish`, `data-subagent-error`, `data-terminal-info`

## Versioning rule

- Additive changes (new optional fields, new routes, new enum members that clients may ignore) stay
  in `/api/v1` and must update `openapi.json` in the same pull request.
- Removal or renaming of a field, route or meaning is a breaking change → `/api/v2`, with v1 served
  until the iOS app is updated.
- Renaming a route handler also changes its generated `operationId`, which is a breaking change for
  generated clients (the Swift generator filters by operationId).

## The iOS subset

The Swift client filters to these operations (exact paths in `openapi.json`):

| purpose | path(s) |
|---|---|
| login / refresh / logout | `POST /auth/jwt/token`, `POST /auth/jwt/refresh`, `POST /auth/jwt/revoke` |
| current user | `GET /users/me` |
| workspaces | `GET /api/v1/workspaces` |
| folders | `GET /api/v1/folders` |
| documents list / detail / status | `GET /api/v1/documents`, `GET /api/v1/documents/{document_id}`, `GET /api/v1/documents/status` |
| upload | `POST /api/v1/documents/fileupload` |
| URL save | `POST /api/v1/documents/url` |
| delete | `DELETE /api/v1/documents/{document_id}` |
| document search | `GET /api/v1/documents/search` |
| chunk lookup (citations) | `GET /api/v1/documents/by-chunk/{chunk_id}` |
| chat threads | `GET /api/v1/threads`, `POST /api/v1/threads`, `GET /api/v1/threads/{thread_id}/messages` |
| chat turn (SSE) | `POST /api/v1/new_chat` (stream format above; not generated) |
