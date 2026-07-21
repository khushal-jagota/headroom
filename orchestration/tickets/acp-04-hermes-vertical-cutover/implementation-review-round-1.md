# ACP-04 implementation review — round 1

## Verdict

`NOT READY`

## Reviewer output

1. **P1 — Phase 8 is materially incomplete.**
   `tests/e2e/test_acp_conversation.py` has one `TestClient` WebSocket test, not the required
   UI → Vite proxy → uvicorn → FastAPI path. It also does not prove most cases 1–8.

   - Case 1: partial — attach/prompt/audit exist, but sequence-1 reset/load/ready ordering and
     Ticket mirror agreement are not asserted.
   - Case 2: missing — refresh reuses the same server/child; no fresh-server durable restart or
     cursor floor.
   - Case 3: partial — generation increments, but no simultaneous second browser, atomic mirror
     assertion, or stale-source rejection.
   - Case 4: missing — worker starts after a browser; no worker-first/no-browser stream or mid-turn
     private replay.
   - Case 5: partial — positive permission response only; no attached-owner rejection or stale
     worker permission race.
   - Case 6: partial — typed worker text is covered; callback-before-subject, exact interrupt,
     rejection/capture failure, collector teardown, and queued-successor ordering are missing.
   - Case 7: missing — no slow-consumer or reset-overflow proof.
   - Case 8: partial — real child audit exists, but no negative proof that DB chat/event rows alone
     do not affect worker context.

   Minimal correction: use the existing e2e server/browser fixtures with the injected SDK child and
   add deterministic protocol/latch cases.

2. **P1 — stale old-session ingress can kill the replacement conversation.**
   `hub.py` flushes every captured notification based only on child source identity. New conversation
   preserves the child generation/record identity, so a late notification for the old ACP session is
   passed into the new binding; envelope validation then raises on the session mismatch and the
   source-failure path closes the live child. This violates Phase 8 case 3's requirement to reject
   stale source updates. Filter or ignore exact old-session notifications before publication.

3. **P2 — active replay capacity has an off-by-one.**
   `hub.py` permits a replay buffer whose length equals queue capacity, fills the queue, then globally
   publishes `ready`, immediately evicting that new browser as a slow consumer. Reserve one slot for
   `ready` or classify it as replay unavailable before enqueueing.

4. **P2 — production startup failure bypasses composition cleanup.**
   `server.py` builds the composition and starts loops before entering the lifecycle cleanup scope.
   If loop startup raises, `close_admission()` and `shutdown()` never run. Enclose post-composition
   startup in the cleanup scope.

## Orchestrator disposition

All four findings were accepted. The original implementer corrected only these findings and reran
the focused gate. The same reviewer then performed the bounded correction check recorded separately.
