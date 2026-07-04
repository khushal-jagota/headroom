# T18 — E2E harness + items 22–27 (stage 6)

## Scope

The Playwright foundation and the first seven e2e acceptance tests (SPEC §18.3 items 22–27), names `test_e22_*` … `test_e27_*`, one test per item, assertions on the spec's exact values.

Harness requirements (SPEC §18.3 preamble): real server subprocess + temp SQLite DB + `PLAN_TEST_MODE=1`; ticks via `/api/test/*`; time via `PLAN_FAKE_NOW` env at boot and `/api/test/set-now` during; two browser contexts where stated; fakes for all external boundaries (adapter selection via env: fake spawn/boundary, echo or offline gateway per test).

## Files owned

- `tests/e2e/conftest.py` — the server fixture: free-port test server subprocess (env-configured temp DB per test, PLAN_TEST_MODE=1, adapters faked, fake-now baseline), readiness wait on /api/meta, teardown kill; two-context browser fixtures (pytest-playwright); helpers: `cli(...)` runner invoking `.venv/bin/plan` via subprocess with env pinned to the test server (PLAN_SERVER_URL, PLAN_TICKET_ID, PLAN_RUN_ID, PLAN_CLAIM, PLAN_ACTOR as each test needs), API client for assertions.
- `tests/e2e/test_flows_a.py` — items 22–27:
  22. `plan ticket create` via subprocess → ticket appears on Board in `needs_success` in a SECOND context without reload (WS invalidation).
  23. `plan propose success` with PLAN_TICKET_ID env + multi-line markdown stdin → Ticket screen shows the proposal rendered intact.
  24. Review: pending proposal appears; Accept impossible without the grant pair (control disabled until both picked); choosing ("no further", stop) advances one state, ceiling == new state and at_cap == stop asserted via API; item leaves queue; both contexts update without reload.
  25. Edit-accept: textarea prefilled with proposal; altered text + grant (needs_plan, propose) → stored exactly as value on Ticket; ceiling/at_cap set accordingly.
  26. Chat: echo fake → sent message renders a reply; chat_session_key persisted (API); offline fake (second server or env-switched) → offline notice.
  27. Auto-accept chain: ceiling needs_plan/at_cap propose ticket; two CLI proposals → needs_plan with plan proposal pending in approval queue.

## Constraints

- Selectors: stable data-* attributes; if a screen lacks a hook you need, request it via report — the UI tickets own the screens (small additive data-attributes are permitted as a coordinated exception, listed in your report).
- No sleeps for state: wait on conditions (Playwright expect polling). WS updates must be awaited as UI changes, not by reloading.
- Every test runs headless chromium and passes under `./verify` (which invokes `pytest tests/e2e`).

## Acceptance for integration

`.venv/bin/pytest tests/e2e/test_flows_a.py -q` green headless; `./verify` scoreboard flips items 22–27 to PASS; no skip-scan violations.

**Fence reminder:** exactly ONE test whose name starts with each item's `test_eNN_` anchor may exist across the whole e2e suite — the scorer fails an item with multiple anchored matches. Supplementary tests use non-anchored names.
