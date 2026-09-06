# Use the existing status revision to identify a claim

Small root-owned integration repair, with planning/implementation collapsed because it
substitutes an existing monotonic identity in one guard and its three consumers. No new
state or conversation behavior is introduced. The final combined independent review
will check this patch.

A release currently compares status and second-resolution timestamp. A legitimate
release/reclaim in the same second returns to the same pair, so a delayed old release
can erase the fresh claim. `ticket_status_revision` already increments on every actual
status transition. Use that existing fact for claim identity; timestamps remain display
facts. Preserve the canonical status writer, blockers, send/refusal behavior and all
protected conversation implementation byte-for-byte.

Allowed changes: release_worker_step_claim and its docstrings in tickets/data.py;
argument passing in worker_step_readiness_loop.py and sprints/supervisor_service.py;
Ticket timestamp/revision comments; existing claim tests; current runtime docs if they
state the old timestamp contract. No other behavior or storage changes.

Acceptance: existing stale-release behavior and a same-second release/reclaim reject the
old claim while leaving the new worker claim intact. Adapt the existing stale-release
case to the stronger same-second example, without adding duplicate cases. Run the
claim-focused tests, reserve ./verify for the whole deeper program's final settled tree.

Focused result: claim/release selection across the existing engine and readiness-loop
suites passed all 8 selected tests. The stale loop proof now uses identical timestamps
for the original claim, release, re-claim and rejected late release.
The two existing supervisor restart cases also passed, covering the third caller.
An initial command used the wrong singular filename and collected nothing; the corrected
plural filename produced this result. No full verification was run.
