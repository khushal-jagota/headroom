# T14 — UI foundation: tokens, shell, data layer, primitives (stage 5)

## Scope

The no-build frontend's foundation: design tokens finalized, app shell with hash routing, the fetch + WS-invalidation data layer, the markdown renderer, and the primitive components (D11 inventory items 1–6, 17). Screens land in T15–T17 on top of these.

Contracts: the API route table (T01 plan §13) and the JSON the T10 endpoints actually serve (read them on disk); decisions.md D11 (the component inventory is binding); PRINCIPLES.md design system section; SPEC §10 (simplicity ruling), §9 (WS as invalidation only, debounce 250ms from /api/meta), §14 (audio seam: chat input as pluggable source), §15 (non-goals — no command palette, no keyboard layer, no drag-and-drop, no instructional empty states).

## Files owned

- `assets/tokens.css` — refine starter values into the real personality: restrained dark surfaces, one deliberate accent, five-size type scale, spacing rhythm, motion tokens (fast/base/slow; entrances ease out, nothing bounces). No new token categories.
- `assets/app.css` — all component styling, exclusively via tokens (zero literal colors/durations/sizes outside tokens.css).
- `assets/config.js` — frontend constants module (route paths, debounce default until /api/meta loads).
- `assets/api.js` — fetch wrapper (JSON, structured-error passthrough), WS client (`/api/events?since=`), invalidation bus: WS batch → debounced refetch notification to the active screen (250ms from /api/meta); reconnect with backoff; NO client-side state store — screens always refetch.
- `assets/markdown.js` — minimal safe renderer (headings, bold/italic/code, fenced blocks, lists, links, paragraphs); escapes HTML; no external libs.
- `assets/components.js` — D11 items 1 App shell (nav + Review badge from /api/queues), 2 Panel, 3 Markdown block, 4 Field editor, 5 Meta chips, 6 Entity row, 17 Error line. Classic-script component functions returning DOM nodes; no framework, no build.
- `assets/app.js` — hash router (#/day, #/review, #/board, #/ticket/<id>, #/sprint, #/backlog), screen registry (screens register a render(root, params) — T15–T17 plug in), boot sequence (/api/meta → WS connect → route).
- `src/planner/core/server.py` — ONLY the `GET /` handler block: serve the real index shell (inline HTML referencing the classic scripts in order). Touch nothing else in the file.

## Constraints

- Every JS file passes `node --check` (classic scripts, no modules/import syntax).
- Refresh restores state (hash routing + refetch); no state survives outside the URL.
- Add nothing beyond the inventory; empty states are one quiet line, never instructional prose.

## Acceptance for integration

Server boots; `/` renders shell with nav; screens show "not built yet" placeholders wired through the router; WS connects and a manual event append triggers one debounced refetch callback (observable via console hook); node --check green on all six JS files; ruff/mypy unaffected.
