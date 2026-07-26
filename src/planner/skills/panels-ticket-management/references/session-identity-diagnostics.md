# Cross-Ticket worker identity diagnostics

Use this when a Panels worker appears to believe it owns another Ticket, especially when `panels worker my-ticket` returns a plausible but wrong record.

## What identity is

A Ticket names its conversation in one place: `tickets.conversation_id`. That is the whole of it. There is no binding table, no second owner, and no live-versus-durable pair to reconcile — the backend process's own session id belongs to the conversation system and is rebound without anything outside it noticing.

So there are only two ways a worker can be looking at the wrong Ticket: two Ticket rows carry the same conversation id, or the worker was handed the wrong id in its environment.

## Isolate the boundary before proposing a fix

1. **Confirm the source Ticket.** Read the affected Ticket and record its canonical id, Worker type, Stage, and `conversation_id`. Do not infer identity from the currently open UI route.
2. **Check for a duplicate.** Ask the database whether any conversation id appears on more than one Ticket. If one does, that is the fault and everything below is a symptom.
3. **Run a controlled CLI lookup.** Invoke `panels worker my-ticket --json` with each conversation id supplied explicitly. If each id resolves its own Ticket, the lookup is behaving and the caller is supplying the wrong identity.
4. **Check what the worker was actually launched with.** Read the spawn environment the worker is running under without printing unrelated values or credentials. A correct Ticket row plus a wrong environment localizes the fault to how the worker was started, not to how identity is stored.

## Reporting checkpoint

After steps 1–4, tell the user the rough result before doing more. State separately:

- whether the Ticket rows themselves are correct and unique;
- which identity the CLI actually consumed;
- what the worker was launched with;
- what has not yet been proven.

Then ask whether to create a Ticket. Do not continue into patches, restarts, regression suites, or canonical verification without that approval.

## Ticket acceptance shape

A complete follow-up Ticket should require:

- a two-concurrent-Ticket reproduction;
- a uniqueness check over `tickets.conversation_id`;
- explicit missing/ambiguous fail-closed behavior rather than selecting the first matching Ticket;
- focused regression tests plus one clean canonical verification;
- review of any diagnostic patch as evidence, not as an already-approved solution.
