# Implementation review disposition

Disposition for Codex implementation-review findings.
Finding 3 was already fixed in the incoming diff and is preserved.

1. **Service-stop interruption can still mark recoverable ticket work `errored`.**
   Accepted and fixed. `EmployeeStepRunner` now records a stopping state when stop closes admission. During that state, gateway interruption/error settles the visible worker turn as `interrupted` and leaves the Ticket at `agent_running_step` with its session key unchanged. Ordinary interruption outside stop still errors the Ticket.

2. **Ordinary chat startup recovery blocks startup on model completion.**
   Accepted and fixed. `recover_human_turn` now performs validation/rollover/admission synchronously, starts `_run_human_turn` in a daemon thread, and returns. Server startup coverage proves the recovered stream is entered, `start_background_loops` is called before release, and the recovered turn then settles normally.

4. **Ordinary chat recovery does not reject session-key mismatch.**
   Accepted and fixed. Recovery validates the stale active turn's `session_key` against the authoritative entity key before rollover. Null or mismatched stale keys fail the stale row with explicit recovery errors and do not create a replacement turn, call the gateway, or remint.

5. **Broad `TypeError` fallbacks hide shutdown bugs and drop the absolute deadline.**
   Accepted and fixed. The fallback retries were removed from core loops, server shutdown, and routed gateway shutdown. Production lifecycle interfaces and test fakes now accept keyword-only `deadline`. Tests prove internal `TypeError` propagates without retry, routed gateways receive the same absolute deadline, and LiveSessionManager deadline failure clears `_closing` and signals waiters.

6. **Required browser proof is missing.**
   Accepted and fixed. A Playwright test now seeds a durable running ordinary ticket chat turn before server boot, enables a narrowly named test-mode startup-recovery switch, boots `panels serve`, opens the ticket chat UI, and asserts the browser-visible transcript preserves the original input once, preserves the old partial assistant output, shows the system recovery turn, and settles the continuation output. The same startup recovery coordinator is exercised through FastAPI lifespan; the test does not call the recovery writer/service directly and adds no endpoint or UI redesign. The exact named Playwright test passed from the normal ticket worktree after the Codex sandbox itself proved unable to launch Chromium.

## Rereview finding

7. **Worker restart recovery did not reject a stale visible-turn session mismatch.**
   Accepted and fixed. Ticket recovery now passes the authoritative `tickets.chat_session_key` into the atomic rollover writer. A null or mismatched stale worker-turn key settles that visible turn as errored, marks the still-running ticket errored through the canonical writer, submits nothing to Hermes, and creates no recovery message. The new regression test failed before the fix because `session.resume` was called; after the fix, the focused recovery set passed (`4 passed`).
