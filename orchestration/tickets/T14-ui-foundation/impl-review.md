# T14 implementation review — codex output + orchestrator dispositions

Codex (gpt-5.5, xhigh, read-only sandbox) reviewed the implemented files against ticket.md, plan.md (incl. §13 amendments), D11, PRINCIPLES design system, SPEC §9/§10/§14/§15. Full transcript: impl-review-raw.txt. Verdict: `VIOLATIONS: 3` (1 HIGH, 1 MEDIUM, 1 MINOR). Codex could not run the smoke itself (its read-only sandbox blocks Playwright browser launch); the orchestrator ran it after every fix.

Clean categories confirmed by codex: server.py diff confined to `_SHELL` (assert/lifespan/handlers/routers/meta/WS/test-router/mount untouched); single real `innerHTML` (markdown.js render); escape-before-parse and NUL-sentinel hygiene; zero client state store (no entity cache, no `msg.events` reads); zero literal colors/sizes/durations in app.css; no import/export/await, node --check green on all five JS files; components exactly D11 items 1–6 + 17, no near-duplicates; no §14/§15 violations (no chat/audio coupling, palette, keyboard layer, DnD, instructional empty states).

## Findings and dispositions

**F1 (HIGH) — markdown.js safeHref: leading-C0-control `javascript:` bypass.** ACCEPTED, FIXED. A URL like `\u0001javascript:alert%281%29` survives escaping (C0 controls are not in the escape set, not `\s`, so the link regex admits it), read as scheme-less by the old check, yet the WHATWG URL parser strips leading C0 controls/spaces — the browser executes it as `javascript:` on click. Fix shipped: `safeHref` now validates a normalized copy with `/[\u0000-\u0020]+/g` stripped (a strict superset of browser normalization, so nothing that passes can normalize into a non-whitelisted scheme; the emitted href stays the escaped original). Verified with a 14-case harness (leading/doubled/interior C0, tab-prefixed, plain `javascript:`/`data:`/`vbscript:` all rejected; https/http/mailto/relative/hash/query forms all allowed) — all correct. One incident during the fix: the orchestrator's first two edit attempts embedded literal control bytes (incl. NUL) into the source instead of `\u` escape text, which flipped grep into binary mode and would have broken the audit fences; caught immediately (grep went silent), rewritten via a Python patch, `node --check` + zero-NUL confirmed.

**F2 (MEDIUM) — smoke's flush assertions watched only `__plannerDebug.flushes`, which increments inside the bus regardless of whether app.js ever registered the invalidation callback.** ACCEPTED, FIXED. Check c1 now also tags the live `.screen` element with `data-smoke` before the append and asserts the attribute is gone after the flush — `route()` builds a fresh screen div per render, so the marker can only disappear if the registered callback actually re-rendered the active screen. Bus→app wiring is now proven, not assumed.

**F3 (MINOR) — router accepted trailing extras (`#/day/junk`, `#/ticket/t_x/junk`).** ACCEPTED, FIXED. `route()` now requires exactly one segment for plain screens and exactly two for ticket; anything else renders the quiet "no such screen" line.

## Post-fix gate run (all fresh, orchestrator-run)

- `node --check` on all five assets/*.js: green
- `grep -nE '\b(import|export|await)\b' assets/*.js`: empty
- app.css literal grep (colors/px/rem/em/ms/s): empty
- `.venv/bin/ruff check .`: All checks passed (one E501 introduced by the F2 fix, fixed immediately)
- `.venv/bin/mypy src/`: Success, 84 files
- `.venv/bin/pytest tests/unit`: 81 passed
- smoke: `SMOKE PASS (7 checks)` — including the strengthened c1
