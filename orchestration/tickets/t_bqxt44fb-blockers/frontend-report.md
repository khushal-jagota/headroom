# Frontend Report — t_bqxt44fb Blockers

## Scope

Implemented only the frontend, invalidation, browser-test, and report slice in the isolated worktree.
No backend contract shape was changed, no live database was touched, no commit or merge was made, and
`./verify` was not run.

The frontend consumes the frozen backend shape from `backend-report.md`:
`blocker_summary: { blocked, blocked_by, blocks }`.

## RED/GREEN Evidence

### Event Mapping

RED command:

```sh
npm --prefix web test -- event-mapping
```

RED result:

```text
AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal
actual omitted ticket:t_blocked and item:si_blocked from a state_changed event carrying
affected_blocked_target_ids.
```

GREEN command:

```sh
npm --prefix web test -- event-mapping
```

GREEN result:

```text
> test
> node tests/event-mapping.test.mjs event-mapping
```

### Browser Regression

New focused Playwright test:

```sh
.venv/bin/python -m pytest tests/e2e/test_blockers_frontend.py::test_ticket_blocker_summary_links_and_sprint_item_hash_selection -q
```

Initial result before implementation:

```text
ERROR at setup ... BrowserType.launch: Target page, context or browser has been closed
FATAL: ... bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

Post-implementation result after rebuilding frontend assets:

```text
ERROR at setup ... BrowserType.launch: Target page, context or browser has been closed
FATAL: ... bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

Fallback browser attempts:

```sh
.venv/bin/python -m pytest tests/e2e/test_blockers_frontend.py::test_ticket_blocker_summary_links_and_sprint_item_hash_selection --browser firefox -q
.venv/bin/python -m pytest tests/e2e/test_blockers_frontend.py::test_ticket_blocker_summary_links_and_sprint_item_hash_selection --browser webkit -q
```

Fallback results:

```text
Firefox executable doesn't exist at .../ms-playwright/firefox-1532/...
WebKit executable doesn't exist at .../ms-playwright/webkit-2311/...
```

Browser evidence is therefore blocked before app code executes in this sandbox. The regression is present
and should be rerun by the integrator in an environment where Playwright can launch Chromium.

## Focused Green Evidence

```sh
npm --prefix web test -- event-mapping
```

Result: passed.

```sh
npm --prefix web run check
```

Result: passed with 0 errors and the existing 3 `TicketRoute.svelte` warnings about initial `id` capture.

```sh
npm --prefix web run build
```

Result: passed. Vite rebuilt `web/dist` and emitted the existing runtime asset warnings for
`/assets/markdown.js`, `/assets/tokens.css`, and `/assets/app.css`, plus the existing 3
`TicketRoute.svelte` warnings.

```sh
.venv/bin/python -m py_compile tests/e2e/test_blockers_frontend.py
```

Result: passed.

```sh
git diff --check
```

Result: passed.

Bundle confirmation:

```sh
.venv/bin/python - <<'PY' ... PY
```

Result:

```text
DIST-BUNDLE-CURRENT index-r-Ug3uQu.js
```

A disposable served-bundle HTTP check was attempted but blocked by the sandbox before server boot:

```text
PermissionError: [Errno 1] Operation not permitted
```

## Files Changed

- `assets/app.css`
- `web/src/App.svelte`
- `web/src/lib/eventMapping.mjs`
- `web/src/lib/types.ts`
- `web/src/routes/SprintRoute.svelte`
- `web/src/routes/TicketRoute.svelte`
- `web/tests/event-mapping.test.mjs`
- `tests/e2e/test_blockers_frontend.py`
- `web/dist/index.html`
- `web/dist/assets/index-r-Ug3uQu.js`
- `web/dist/assets/index-Bhsd7ag6.js` removed by rebuild
- `PROGRESS.md`
- `decisions.md`
- `orchestration/tickets/t_bqxt44fb-blockers/frontend-report.md`

## Behavior Implemented

- Ticket detail types now include the frozen `blocker_summary` shape.
- Ticket detail renders one read-only `Blockers` section after Recap and before gated fields.
- `Blocked by` rows link to existing Ticket routes and show active versus cleared state.
- `Blocks` rows link to existing Ticket routes or `#/sprint?item=<id>` for Sprint items.
- No blocker controls, graph, header behavior, editing behavior, or gated-field behavior were added.
- `state_changed` events carrying `affected_blocked_target_ids` invalidate each affected Ticket/Sprint
  item resource plus board, queues, and current sprint aggregates.
- Existing `link_added` and `link_removed` invalidation remains covered.
- `#/sprint?item=<id>` keeps the existing Sprint screen and opens, scrolls, focuses, and marks the
  matching item row.

## Remaining Risks

- The focused Playwright regression could not run to completion in this sandbox because no browser engine
  can launch here. The integrator must rerun it and the relevant Ticket/Sprint browser modules in a
  launchable browser environment before claiming browser GREEN.
- The disposable HTTP served-bundle check was blocked by local socket permission. The on-disk production
  bundle was rebuilt and internally checked instead.
