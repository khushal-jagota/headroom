# Current-sprint to today triage

Use this when the sprint contains many old, overlapping, or partially migrated Tickets and the owner asks what belongs on today’s board.

## Selection pass

1. Read today’s tickets and keep the open set visible.
2. Read the current sprint and recursively extract non-terminal Tickets, including Tickets nested under sprint items and loose sprint Tickets.
3. Subtract today’s IDs, then rank by sprint importance, concrete next action, and overlap—not priority alone.
4. Read full JSON for the top candidates before recommending them.

A useful recommendation normally contains only one or two Tickets:

- the sprint’s primary bet when its next gate is a real owner/agent decision;
- a bounded publication or closeout Ticket for work likely already built.

Exclude older implementation Tickets when a newer publication Ticket represents the remaining work. Exclude broad landing/gate Tickets when a concrete integration Ticket is already active today. Leave stale low-priority cleanup out unless it solves a current blocker.

## Waking a current-state investigation

When the owner believes a publication/closeout Ticket may already be complete:

1. Preserve that belief as a ticket-level note asking for read-only current-state inspection: repository/remote state, effective non-secret configuration, deployed path, and available verification evidence.
2. Do not expose credentials or mutate production merely to test the hypothesis.
3. Check field/state coherence before day placement. If the current gate’s canonical field is already settled but `state` still points at it, inspect history and advance only to the next blank gate through the supported direct state operation.
4. Set the ceiling to that blank gate with `at_cap=propose`, then add the Ticket to today.
5. Verify that the worker actually started and report the resulting state/status.

The worker’s job is to establish **done / remaining / uncertain** with evidence. If reality is already complete, use Chief external-work reconciliation afterward rather than manufacturing more implementation work.

## Follow-up regressions from merged work

A session worktree may be behind `main` even when the user is exercising a merged feature. Inspect the merged/live branch directly and read the tests that shipped with it. For UI races, distinguish what existing coverage proves from the interleaving the user actually reported. A follow-up Ticket should require a deterministic red browser reproduction before any fix and should preserve adjacent established behavior.