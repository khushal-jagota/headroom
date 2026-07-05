# T14 report — UI foundation

## What was built

The no-build frontend foundation, stage 5. `assets/tokens.css` now carries the app's whole personality: achromatic neutral-dark surfaces, one amber accent (`#e2a33e`) with a fixed meaning — the accent marks "needs the human" (P0, pending proposal, auto-blocked, overdue, review badge, errors, focus, primary action) — an even 4/8/12 radius scale, 90/150/240ms motion with one ease-out curve, the starter spacing rhythm kept, and a closed five-size type scale (11/13/15/19/24px) plus two font stacks. Five classic scripts compose under `window.Planner` with zero build tooling: `config.js` (constants), `api.js` (fetchJson with PlannerError envelope passthrough + the invalidation bus: WS `/api/events?since=` client that reads only `msg.cursor`, trailing 250ms debounce from `/api/meta`, exactly one subscriber, reconnect with 500-to-10000ms doubling backoff), `markdown.js` (escape-first safe renderer, single `innerHTML` in the codebase, scheme-whitelisted hrefs hardened against C0-control normalization bypass), `components.js` (the seven D11 primitives, `createElement`/`textContent` only), `app.js` (hash router with exact route shapes, screen registry with overwrite-wins for T15–T17, quiet one-line placeholders, meta-then-WS-then-route boot). `app.css` styles everything exclusively through `var(--token)` — the literal-value grep is empty. `server.py` changed in exactly one place: the `_SHELL` string now serves the real shell (tokens.css, app.css, then the five scripts in dependency order). `smoke.py` (ticket folder, per T10 convention) boots in-process uvicorn in test mode and drives headless chromium through seven checks.

## Component list shipped vs D11

| D11 item | Shipped as | Note |
|---|---|---|
| 1 App shell | `appShell()` | Returns the shell DOM node with `content`/`setReviewBadge`/`setActiveNav` attached as properties (codex C2 fix). **Deviation: five nav links (Day, Review+badge, Board, Sprint, Backlog), not D11's "six screen links"** — `#/ticket/<id>` needs an id; SPEC §10 reaches Ticket via cards/links; a permanently inert nav entry fails SPEC §10's add-nothing bar. Reversal is one array entry if the integrator reads D11 literally. |
| 2 Panel | `panel(title, children)` | The only box primitive. |
| 3 Markdown block | `markdownBlock(text)` | Empty input renders one quiet `(none)` line. |
| 4 Field editor | `fieldEditor(value, onSave)` | Save disables during flight; rejection renders an Error line inline; no optimistic UI. |
| 5 Meta chips | `chip(variant, value, opts)` | One component; variants priority/state/project/deadline + markers pending-proposal, running-claim, blockers-cleared, auto-blocked, frozen. Accent appears exactly where a human is needed. |
| 6 Entity row | `entityRow({title, href, chips})` | Native anchor — URL is the only state. |
| 17 Error line | `errorLine(err)` | Renders exactly what fetchJson rejections carry (code + message). |

Items 7–16 are T15–T17 scope, not built (correct for this ticket).

## Pipeline record

- plan.md (Fable planner) -> plan-review.md (codex, 5 findings: 1 fixed pre-implementation via amendments, C2/C5 fixed in code, C3 refuted, C1/C4 recorded) -> plan §13 binding amendments -> implementation (Opus, all self-checks green first pass) -> impl-review.md (codex, 3 findings, ALL ACCEPTED AND FIXED: HIGH C0-control `javascript:` href bypass, MEDIUM smoke not proving the subscriber ran, MINOR loose route shapes).
- Codex ops note for the run: `codex exec` hangs when launched from a background shell because it blocks reading stdin — it printed "Reading additional input from stdin..." and idled indefinitely (two hangs, ~50 min lost). **Fix: append `< /dev/null`.** Both later reviews completed normally with it.
- Second ops note: the orchestrator's output channel can mangle backslash-u escape text in Edit calls into literal control bytes — this briefly put a literal NUL into markdown.js (flipping grep into binary mode, which would have silently broken the audit fences). Caught immediately and repaired via Python-scripted patches; all fence greps re-verified clean afterwards.

## Gate results (final state, all run fresh by the orchestrator)

- `node --check` on all five assets JS files: green (the ticket acceptance's "six JS files" is a miscount — five are owned/exist; plan-review.md S3).
- `grep -nE '\\b(import|export|await)\\b' assets/*.js`: empty. CSS literal grep on app.css: empty.
- `.venv/bin/ruff check .`: All checks passed.
- `.venv/bin/mypy src/`: Success: no issues found in 84 source files.
- `.venv/bin/pytest tests/unit`: `81 passed, 1 warning in 1.34s`.
- `smoke.py`: all seven checks —

```
ok 01 — in-process server up on 8790; /api/meta 200
ok 02 — (a) shell references present, strictly ordered; all 7 assets served
ok 03 — (b) /api/meta = ui_debounce_ms 250, ws_poll_ms 50, test_mode True
ok 04 — shell mounted; default route #/day; placeholder wired; WS open
ok 05 — (c1) one append -> one debounced flush; screen re-rendered; console hook observed
ok 06 — (c2) two rapid appends -> one flush; cursor == 3
ok 07 — router nav: all six route shapes render placeholders
SMOKE PASS (7 checks)
```

## Deviations from ticket

1. Five nav links vs D11's "six screen links" (above; deliberate, documented, cheap to reverse).
2. None otherwise: owned files only (assets/* new/rewritten, server.py `_SHELL`-only — codex confirmed the rest of the file untouched — plus ticket-folder pipeline artifacts).

## Concerns for the integrator

- The WS reconnect/backoff path is implemented per plan but not smoke-tested (needs a server bounce mid-test); worth covering in the stage-6 e2e wave.
- The two-rapid-appends coalescing check has a residual timing dependency (two localhost POSTs must land within ~230ms of each other; typical spread <20ms). Remedy on any future flake: widen the settle sleep, never weaken the exactly-one assertion (amendment A5).
- T15–T17 consume: `Planner.registerScreen(name, render(root, params))` (overwrite-wins; add their script tags after app.js in `_SHELL`), the seven `Planner.components.*` functions, `Planner.api.fetchJson`, `Planner.markdown.render`. The bus needs nothing from them — screens are stateless renders.
