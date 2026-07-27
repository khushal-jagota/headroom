# Diff review disposition — codex gpt-5.6-sol on eeda828c..HEAD, 2026-07-25

Eight findings (4 blocking, 4 advisory). All eight accepted and fixed; no refutations.

1. **Door misses autocommit writes (BLOCKING) — accepted, fixed.** `isolation_level=None` means
   bare DML (no BEGIN) commits immediately and the transaction-closing detection never fires;
   real writers exist (employee-step repository updates from startup settlement and bare runner
   paths). Fix: the door also emits when a statement runs with no transaction open before or
   after AND the connection's `total_changes` grew — "a write that leaves the database changed
   with no transaction open has committed". Bare SELECTs, zero-row updates, and DDL stay silent.
   New door tests cover all four cases.

2. **Retained cutover script broken (BLOCKING) — accepted, fixed.** `scripts/
   migrate_current_sprint_from_v1.py` passed the removed `cause=` argument and counted the
   dropped `events` table in its applied summary. Repaired minimally; scripts/ and ops/ swept for
   other references to removed names.

3. **BoardRoute redirect guard weakened (BLOCKING) — accepted, fixed.** `!isFetching` treated a
   failed background refetch as settled where the old cache's `stale` flag deliberately did not.
   Guard now also requires the query not be in error; other converted guards checked for the same
   pattern. `web/dist` rebuilt.

4. **AGENTS.md still teaches the wake module (BLOCKING) — accepted, fixed.** The system-map
   bullet now describes `core/change_signal.py`, door-owned announcements, the discovery-loop
   subscription, and `GET /api/changes`.

5. **Shutdown test could pass vacuously (ADVISORY) — accepted, fixed.** `opened` is now set only
   after the reader receives the HTTP response headers from `/api/changes`.

6. **Transport coverage gaps (ADVISORY) — accepted, fixed.** Added an SSE test emitting from a
   worker thread (exercises the `call_soon_threadsafe` boundary) and restored the `/api/health`
   `expected_sha` assertions lost with `test_server_events.py`.

7. **Non-positive heartbeat hot-loops (ADVISORY) — accepted, fixed.** `sse_heartbeat_ms <= 0`
   now refuses at config load.

8. **Docs overstate both boundaries (ADVISORY) — accepted, fixed.** The
   `employee_configuration.py` raw-SQLite exemption is now stated in `db.py`'s docstring,
   `docs/frontend.md`, and `docs/employee-runtime.md`; the "every browser read is catalogued"
   claims in `queryCatalogue.ts` and `docs/frontend.md` are softened to live-updated reads.

The review confirmed: wrapper collapse preserved validation/authorization/transaction
boundaries; the resolution engine is intact; the migration chain, backfill ordering, fallback,
and timestamp writers are correct; SSE and browser lifecycles are otherwise sound; and six of the
nine plan-review dispositions landed fully (the other three are exactly findings 1, 6, and 8
above, now closed).
