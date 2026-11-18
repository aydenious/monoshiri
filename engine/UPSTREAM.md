# Upstream provenance

`engine/` is a fork of **SurfSense v2.1.0** — https://github.com/MODSetter/SurfSense,
commit `7fb479c361414e1ffd180860eb0b5eb5afb0c4e3` (app `VERSION` 0.0.40). The live stack ran the
official images `ghcr.io/modsetter/surfsense-{backend,web}:git-7fb479c` built from this commit.

Imported with `git archive 7fb479c surfsense_backend surfsense_web LICENSE .gitattributes` (+ `docs/contracts`,
the frozen export-bundle contract the backend tests check against)
(no upstream history; the pristine tree is `reference/surfsense-upstream/` at v2.1.0, so
`diff -r` against it shows every Monoshiri change).

Not imported: `surfsense_local/`, `surfsense_mcp/`, `plugins/`, the rest of `docs/`, `docker/`, translated READMEs,
and landing-page media (`surfsense_web/public/homepage/`, `public/demo.mp4`, ~210 MB — only used by the
marketing pages, referenced by URL string, never statically imported).

Licences kept as upstream: `LICENSE` (Apache-2.0, backend), `surfsense_web/LICENSE` (MIT),
`surfsense_backend/app/proprietary/LICENSE` (BSL 1.1).

The `CLAUDE.md` / `AGENTS.md` files inside `surfsense_backend/` and `surfsense_web/` are upstream's
code-level notes; still accurate for the code, but project rules live in the top-level `CLAUDE.md`.
