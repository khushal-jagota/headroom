# Plan review disposition — codex gpt-5.6-sol, 2026-07-25

Nine findings (5 blocking, 4 advisory). Each verified against the code before disposition.
The plan was revised in place; references below are to the revised plan.

1. **EventKind is load-bearing (BLOCKING) — accepted.** Verified: `Decision`/`EventSpec`
   (`tickets/logic/decisions.py`) are the resolution engine's output contract and
   `tickets/data.py` branches on spec kinds (`stage_changed` → child settlement at data.py:358,
   `proposal_filed` → awaiting_approval). Plan changed: `EventKind`/`EventSpec`/`Decision` stay
   untouched as the engine's decision vocabulary; only `append_event` persistence dies; `EventRow`
   dies. Unconsumed kinds recorded as a later-cleanup carry-forward.

2. **SSE holds uvicorn shutdown open (BLOCKING) — accepted.** Verified: uvicorn 0.50 drains
   connections before lifespan shutdown and never force-closes an in-flight h11 stream; the sole
   launch path is application.py's `uvicorn.run`. Plan changed: active-stream registry with
   thread-safe closers + `uvicorn.Server` subclass whose `handle_exit` closes streams first;
   named process test (SIGTERM with open `/api/changes` → exit within grace budget).

3. **QueryClient provider placement (BLOCKING) — accepted.** Plan changed: client created in a
   plain module (shared with the SSE client), provided above App / passed explicitly to App's own
   queries. Svelte bump dropped — lockfile already resolves 5.56.4 (≥ the 5.25 peer minimum).

4. **Missed consumers (BLOCKING) — accepted, with one refutation.** All named consumers are now
   enumerated in the plan with owning tickets (e2e events-insert, CLI verb test, meta assertion,
   vps-status import, production-mount meta fetch, conftest `__plannerDebug` gates, skills text).
   Refuted: the "contradicts `./verify` is the only completeness claim" point — AGENTS.md
   explicitly allows a multi-ticket program to reserve one full `./verify` for its final settled
   tree; this package runs under that program rule (orchestrator instruction), and the final
   canonical run happens at program end, not here.

5. **Transport coverage lost with test_server_events.py (BLOCKING) — accepted.** Plan now names
   backend tests: SSE frame format, heartbeat cadence, coalescing, disconnect-unsubscribe,
   cross-thread emit, shutdown-with-open-stream; plus door tests (one emission per committed
   transaction via both `execute("COMMIT")` and `commit()`, none on rollback/failed commit).

6. **"Single write door" overclaim (ADVISORY) — accepted.** `employee_configuration.py`'s raw
   `sqlite3.connect` catalog-cache writer is now an explicit recorded exemption (conversation
   scope; affects neither resources nor eligibility). Door detection algorithm confirmed to match
   the codebase's two commit idioms.

7. **Migration fixture too thin (ADVISORY) — accepted.** Fixture now requires several historical
   status transitions (latest wins) plus an updated_at-fallback ticket; both ticket INSERT sites
   named as writers of the new column.

8. **Deleted frontend guarantees (ADVISORY) — accepted.** New web tests must cover catalogue
   keys/paths/URL-encoding and mutation invalidation semantics. Trusted-ingress wrong-origin proof
   moves to `/api/conversation`; SSE gets no origin gate (contentless + CORS-blocked reads),
   recorded in the plan.

9. **Skills reference retired history surface (ADVISORY) — accepted.** `panels-ticket-management`
   SKILL.md lines 135/203 added to ticket C's sweep; instructions rewritten to canonical state.
