# T09 plan review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root), pointed at plan.md, ticket.md (incl. binding
seam), SPEC §2/§7.6/§9/§13, D5/D6, the on-disk contracts, and T01 plan.md pinned sections.

## Codex findings (verbatim summary)

1. plan.md §1.2 expands `stale_claim.detail.reason` to `missing_header` and `run_mismatch`;
   T01 plan.md ~845 pins the shape to `"reason": "expired"|"foreign"|"none_active"`. Contract drift.
2. Phase B's T11-landed 200 path can fail on an uninitialized DB: plain `TestClient(app)` never
   runs `create_schema` (only `plan serve` bootstraps, cli/main.py:62).
3. Phase B keys the 501-vs-200 expectation on `find_spec()`, but the seam's failure modes are
   `ImportError` OR `AttributeError` — a landed module missing the function would 501 while the
   smoke expects 200.
4. The live `plan serve` phase never hits the tick endpoints; the ticket's behavior note requires
   "test endpoints respond" on the live server, not only via TestClient.
5. Phase C hard-codes port 8799 with no collision guard; readiness polling could hit a foreign
   server.
6. Check 16's WS close doesn't actually exercise the server's `WebSocketDisconnect` path: with no
   subsequent event, the tailer sleeps and never touches the closed socket.

## Dispositions

1. **Refuted as a violation; ruled, recorded.** The T01 plan's sentence pins the detail's key
   shape (`reason`, `presented_run_id`, `presented_claim`, `active_claim_present`) and the three
   reasons its stage knew about. The T09 ticket (later, more specific, binding here) requires
   `require_claim` to validate **run id + claim token** and to reject the **half-present pair**,
   "with `stale_claim` and a detail naming the mismatch". Those two failure modes did not exist in
   T01's enumeration and cannot be truthfully expressed by it: labelling a run-id mismatch
   `foreign` (a token statement) or a half-present pair `none_active` (a ticket-state statement)
   would misname the mismatch — a real §7.6 violation to dodge a cosmetic one. The contract file
   on disk (`core/errors.py`) pins only the code and "detail names it". Ruling: reason vocabulary
   is the strict superset `expired | foreign | none_active | missing_header | run_mismatch`; the
   three T01 values keep their exact meanings; base detail keys stay exactly T01's four plus
   `ticket_id` (dispatch.data convention). Flagged in the report for the integrator and T10.
2. **Accepted.** Amendment A2: Phase B pre-creates the schema on its own temp DB
   (`db.connect` + `create_schema`) and passes that path as `PLAN_DB_PATH` in the env used by
   `load_config`, so the T11-landed 200 path has a real schema under fake adapters.
3. **Accepted.** Amendment A3: the smoke's expectation probe mirrors `_lazy` exactly —
   `import_module` + `getattr` in a try/except `(ImportError, AttributeError)` — never
   `find_spec`.
4. **Accepted.** Amendment A4: Phase C POSTs both tick endpoints on the live server; expected
   outcome keyed by the same A3 probe (501 with the exact seam body, or 200 with a JSON dict).
5. **Accepted.** Amendment A5: port 8799 stays the default (the ticket's own boot line); the
   smoke first probes it with a socket bind and falls back to an OS-assigned free port if busy.
   Check 15's `test_mode is True` assertion doubles as an identity check.
6. **Accepted.** Amendment A6: after closing WS #1, the smoke appends a further event so the
   server's next send hits the closed socket (exercising the `WebSocketDisconnect` handler), then
   asserts the server is still healthy: `/api/meta` 200 and a fresh WS `?since=0` replays the
   full backlog including the post-close event.

No structural re-plan needed: findings 2–6 are smoke-design hardening; finding 1 is a recorded
vocabulary ruling, not a plan change.
