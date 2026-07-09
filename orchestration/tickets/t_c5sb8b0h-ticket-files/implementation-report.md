# Implementation report

## Delivered

- Added `planner.files`, deriving managed ticket storage from the configured database parent (`files/tickets/<ticket_id>/`) and serving it through a narrow Panels route.
- Rejects unsafe IDs, empty/absolute/dot/backslash paths, residual or double-encoded traversal, missing/directories/non-files, and symlink escapes; containment uses resolved paths plus `relative_to`.
- Sends `nosniff`; only an explicit raster-image/audio/video MIME allowlist is inline. Markdown, HTML, SVG, MIDI, MPEG transport streams, and unknown types are attachments when opened directly.
- Added one frontend `FilePreviewTarget`/`resolvePreview` contract and `FilePreview` component. Read-only `MarkdownBlock` hydrates links through it; every persisted chat role now uses `MarkdownBlock`; editable Markdown never mounts preview components.
- Added `#/preview?source=ticket&ticket=<id>&path=<path>`. Markdown uses the hardened renderer. HTML is fetched as text and assigned to `srcdoc` inside an empty sandbox.
- Embedded Markdown/HTML are compact in-app preview links; images, video, and audio render inline; unknown files remain downloads; ordinary external links remain links.
- Preserved media aspect ratio and capped media in the chat rail after live visual inspection found the first sizing too large.
- Documented the storage split, URL, response policy, and reusable frontend seam in `docs/frontend.md`; rebuilt `web/dist`.

## TDD and integration evidence

Observed RED included missing backend/frontend modules, missing-file exception shape, preview URL space encoding, stale hash-route remounts, collapsed-field browser assertions, literal-dot normalization, cross-origin local-file misclassification, frontend href dot normalization, and over-broad audio/video inline policy.

Focused GREEN after integration:

- `node web/tests/file-preview.test.mjs`
- `npm --prefix web run check` — 0 errors; the three existing `TicketRoute.svelte` warnings remain.
- `npm --prefix web run build`
- `.venv/bin/python -m pytest tests/unit/test_ticket_files.py tests/unit/test_server_static_paths.py tests/e2e/test_ticket_file_previews.py -q` — 28 passed.

The initial coding agent could not launch Chromium inside its sandbox. The integrator reran all three focused Playwright tests outside that sandbox and they passed.

## Live browser evidence

A dedicated temporary Panels server was seeded with real Markdown, HTML, PNG, MP4, and fallback files. Browser inspection confirmed:

- ticket fields and every persisted chat role use the same preview behavior;
- the PNG retains its 64×64 aspect ratio and chat media stays compact;
- the MP4 reported duration 1 second, `readyState=4`, and no media error;
- Markdown opens and renders in the in-app preview route;
- HTML displays inside an iframe with `sandbox=""`, and its script could not set a parent-window value;
- the editable field saved a real keyboard edit, preserved every original Markdown link, persisted no generated preview HTML, reloaded, and reopened with the same hrefs (also asserted in Playwright).

## Review

Codex `gpt-5.5` reviewed the implementation in a read-only sandbox. Three findings were fixed: unsafe frontend dot-segment targets, cross-origin `/files/tickets/...` links being treated as local, and over-broad audio/video inline MIME matching. A follow-up found URL normalization still preceded raw-href validation; that was fixed and covered. Final follow-up: `NO VIOLATIONS`.
