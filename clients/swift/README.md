# MonoshiriAPI — generated Swift client

A SwiftPM package generated from the frozen HTTP contract
(`docs/contracts/openapi.json`) with
[swift-openapi-generator](https://github.com/apple/swift-openapi-generator). The
iOS app does not import this package directly: it vendors pre-generated code via
`monoshiri-ios/scripts/sync-api.sh` and consumes only the contract, the
generator config and `Package.resolved`. The chat SSE stream is not modelled by
the generator (see `docs/contracts/README.md` for its format).

```
clients/swift/MonoshiriAPI/
├── Package.swift
├── Package.resolved                 # committed: exact dependency pins
└── Sources/MonoshiriAPI/
    ├── openapi.json                 # byte-identical copy of docs/contracts/openapi.json
    ├── openapi-generator-config.yaml# generate types + client, filter = iOS subset
    └── MonoshiriAPI.swift           # public namespace / placeholder
```

## Building

Linux check (no Apple SDK needed — the build runs the generator plugin and
compiles the generated code, including the `URLSession` transport through
`FoundationNetworking`):

```sh
cd <repo root>
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/clients/swift/MonoshiriAPI:/pkg" -w /pkg swift:6.0 swift build
```

Expected last line: `Build complete!`.

## Dependency versions

Pinned exactly in `Package.swift` and locked in `Package.resolved` (commit it).
Each release was published more than seven days before it was added — the
repository's supply-chain rule:

| package | version | released |
|---|---|---|
| apple/swift-openapi-generator | 1.10.4 | 2026-01-13 |
| apple/swift-openapi-runtime | 1.10.1 | 2026-02-24 |
| apple/swift-openapi-urlsession | 1.2.0 | 2025-09-10 |

These are the newest releases whose package manifests still build with the Swift
6.0 toolchain. Generator/runtime 1.11 and later require swift-tools-version 6.1
(1.13+ require 6.2), so bumping them means bumping the toolchain used for the
Linux check above (Xcode's Swift on the Mac is already newer).

## Build warnings

The generator prints 92 instances of:

```
warning: Schema "null" is not supported, reason: "schema type", skipping
```

They come from FastAPI encoding optional query parameters and body fields as
OpenAPI 3.1 `anyOf: [<type>, {"type": "null"}]`; generator 1.10.x warns while
skipping the `null` branch and keeps the typed, optional parameter. The
operations are generated and compile; the warnings are cosmetic.

## Regenerating after a contract change

The exporter writes this package's `openapi.json` at the same time as the
committed contract:

```sh
cd engine/surfsense_backend
uv run python ../../scripts/export-openapi.py
```

Then rebuild with the `docker run` above and commit both `openapi.json` files
together. CI's `--check` fails if they diverge.

## Scope

The generator filter (`filter: operations:` in the config) lists exactly the
operationIds the app calls — filtering by path would also pull in every other
method on those paths (account deletion, workspace/folder creation, and so on).
