# T17 implementation review — codex output + orchestrator dispositions

Reviewer: `codex exec --sandbox read-only` over the five T17 files (screens-sprint.js,
screens-backlog.js, the additive components.js block, the appended app.css block, smoke.py)
against ticket.md, plan.md (incl. §10 amendments A1–A4), SPEC §10/§5/§3.1/§3.2/§3.5,
PRINCIPLES.md, the T14 foundation, sprints/api.py + views.py, and seed/demo.py.
Verdict as returned: FAIL (2 findings, both smoke-tightening; zero product-code findings).
Codex also independently confirmed `node --check` green on both new JS files; it could not
execute smoke.py inside its read-only sandbox (no usable temp dir) — the orchestrator ran it
instead (twice, both SMOKE PASS).

## Codex findings (verbatim) and dispositions

1. smoke.py sets `PLAN_WS_POLL_MS="250"` but only asserts `meta["ui_debounce_ms"] == 1500`,
   never `ws_poll_ms == 250` — weakens the A2 env guard.
   **ACCEPTED, FIXED** (orchestrator, small fix): smoke now asserts both
   `ui_debounce_ms == 1500` and `ws_poll_ms == 250` (ok 02).

2. Plan §8 s2 requires the `blocked` and `deferred_next_sprint` group containers to exist AND be
   empty; the smoke only checked existence, so stray rows in those groups would still pass.
   **ACCEPTED, FIXED** (orchestrator, small fix): s2 now additionally asserts
   `[data-status-group="<g>"] [data-item-id]` resolves to zero rows for both groups (ok 05).

## Post-fix verification (orchestrator-run, fresh)

- `.venv/bin/ruff check .` → All checks passed.
- `node --check` on screens-sprint.js, screens-backlog.js, components.js → clean.
- `.venv/bin/python orchestration/tickets/T17-sprint-backlog/smoke.py` → SMOKE PASS (10 checks),
  run twice back-to-back (second run on a fallback port — port-collision path exercised too).
