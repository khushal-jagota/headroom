# T19 — E2E items 28–32 (stage 6)

## Scope

Five e2e acceptance tests on the T18 harness (reuse its fixtures; touch conftest only additively if a fixture is genuinely missing, noting it in the report).

- `tests/e2e/test_flows_b.py`:
  28. Day view: fake boundary adapter; `POST /api/test/tick-boundary` at fake-now 05:01 → brief + proposed tree render; Accept-all adds child tickets to the day list.
  29. Invalidation: root invalidate → fake replan called, replacement tree renders; child invalidate → only that child replaced (others keep status).
  30. Dispatcher e2e: eligible ticket + fake spawn; `POST /api/test/tick-dispatcher` claims it (run visible on Ticket); simulate the worker's CLI: `plan propose result` with the claim env (PLAN_RUN_ID/PLAN_CLAIM from the run) then `plan run close --outcome done` → ticket parks in needs_review; appears in approval queue; approve → done.
  31. Refresh restores state: on Day, Board, and Ticket mid-flow (pending proposal, running claim) reload renders identical content (assert key DOM equivalence before/after).
  32. Sprint view live: `plan item set --status active` via CLI reflects on Sprint screen in both contexts without reload; loose ticket appears in the loose section.

## Acceptance for integration

`.venv/bin/pytest tests/e2e/test_flows_b.py -q` green headless; `./verify` flips items 28–32 to PASS; no fence violations.

**Fence reminder:** exactly ONE test whose name starts with each item's `test_eNN_` anchor may exist across the whole e2e suite — the scorer fails an item with multiple anchored matches. Supplementary tests use non-anchored names.
