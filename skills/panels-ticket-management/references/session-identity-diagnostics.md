# Cross-Ticket session identity diagnostics

Use this when a Panels worker appears to believe it owns another Ticket, especially when `panels worker my-ticket` returns a plausible but wrong record.

## Isolate the boundary before proposing a fix

1. **Confirm the source Ticket.** Read the affected Ticket and record its canonical id, Worker type, Stage, `employee_session_id`, and recent session-binding events. Do not infer identity from the currently open UI route.
2. **Check database ownership.** Query every Ticket owning the affected and comparison Employee session ids. Verify whether ownership is unique and whether a session change was persisted. A correct Ticket row plus a wrong CLI result points away from creation/persistence.
3. **Run a controlled CLI lookup.** Invoke `panels worker my-ticket --json` with each durable session id supplied explicitly. If each explicit key resolves its own Ticket, the existing by-Employee-session lookup is behaving consistently; the caller is likely supplying the wrong identity.
4. **Compare live and durable identity.** Capture the Hermes live-window/session id and the terminal subprocess's session variables without printing unrelated environment values or credentials. Repeat after a fresh session or `/new`. A changed live/durable conversation paired with an unchanged terminal key localizes the leak to gateway/context/environment propagation.
5. **Inspect shared-process reuse.** Trace where the shared gateway binds stored keys to live ids, where per-turn context is installed, and where a long-lived terminal environment is created or reused. Distinguish process-global environment from per-invocation context.
6. **Fail closed in the eventual design.** Missing, conflicting, or multiply-owned identity should produce a clear error rather than selecting the first matching Ticket. Preserve legitimate single-session behavior deliberately.

## Reporting checkpoint

After steps 1–4, tell the user the rough result before doing more. State separately:

- whether Ticket/database state appears correct;
- which identity the CLI actually consumed;
- the most likely propagation boundary;
- what has not yet been proven.

Then ask whether to create a Ticket. Do not continue into patches, restarts, regression suites, or canonical verification without that approval.

## Ticket acceptance shape

A complete follow-up Ticket should require:

- a two-concurrent-Ticket reproduction;
- database uniqueness/integrity checks and session-binding history;
- fresh, resumed, and `/new` session cases;
- stale process-environment and correct per-invocation identity cases;
- gateway restart behavior;
- explicit missing/ambiguous fail-closed behavior;
- focused regression tests plus one clean canonical verification;
- review of any diagnostic patch as evidence, not as an already-approved solution.
