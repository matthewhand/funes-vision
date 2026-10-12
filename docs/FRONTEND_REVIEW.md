# Refined Current frontend — public review manifest

**Scope:** a read-only browser preview with explicit synthetic data. No live ingestion or inference is bundled.

## Reviewed file allowlist

| Paths | Source and rationale |
|---|---|
| `frontend/index.html` | Existing Refined Current UI logic selectively promoted as a new public-branch file; legacy camera mount paths, host-derived cross-camera navigation and personal-looking scene examples scrubbed; synthetic badge and direct-link-safe local routing added |
| `frontend/lucide.min.js` | Lucide v0.469.0 icon library (ISC license); no runtime CDN request |
| `frontend/favicon.ico`, `frontend/icon.svg` | Simple static UI icons |
| `frontend/manifest.json` | New generic synthetic-only PWA metadata |
| `frontend/static/fixture-api.js` | Newly authored same-origin, read-only fixture bridge; refuses every write and outside-origin fetch and disables SSE |
| `frontend/static/fixture-data.json` | Explicitly fictional cameras, disabled inference status, synthesized camera catalogs; no model scores, credentials or home configurations |
| `frontend/images.json`, `frontend/analysis.json`, `frontend/bursts.json`, `frontend/pins.json` | Synthetic scene/caption demonstrations with named virtual cameras and deterministic dates; no private catalogs copied |
| `frontend/*.jpg`, `frontend/thumbs/*.jpg` | 20 copies of public PR #1 sample assets, remapped under non-network-address virtual filenames; validated byte-for-byte by SHA-256 manifest |
| `tests/check_refined_frontend.py`, `tests/check_refined_frontend_inline.cjs` | Stdlib/Node checks for path allowlist, provenance, privacy indicators, denied API writes and syntactic validity |
| `tests/refined_frontend_browser.cjs`, `docs/evidence/refined-*`, `docs/evidence/hero-*` | Actual localhost Chromium screenshots and interactive regression coverage; not recordings of real cameras |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, this review note | Public licensing and scope documentation |

All commits are newly created in the public repository's independently initialized Git history. Public frontend PR is deliberately stacked on synthetic gallery PR #1 so changes to the source code and approval of fictional imagery remain independently reviewable.

## Verifications and limits

- Front/back camera switching, direct browser refreshes and overview dashboard work against synthetic data only.
- Source loads on localhost with all outside-origin `fetch` requests refused and no background SSE stream.
- Every mutation, including attempts against `POST /api/delete`, is rejected with HTTP 403 by the fixture bridge; no changes are saved.
- Both recorded fixtures and display annotations explicitly state that no real detector/LLM has run.
- New fixture JPEG filenames contain no camera IP addresses, while the 20 image hashes match the images already submitted through public PR #1.
- Reviewers must still check image-generation redistribution terms, source-level security, accessibility and installability before merging or declaring the MVP complete.

This preview does not represent production backend readiness, model performance or a completed release.
