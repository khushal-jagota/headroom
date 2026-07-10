# Independent Codex implementation review

Command:

```text
codex exec --skip-git-repo-check -m gpt-5.5 --config model_reasoning_effort="xhigh" --sandbox read-only <review prompt> < /dev/null
```

Full initial output:

```text
1. Severity: Medium — zero-ring coverage misses the worker proposal-with-recap route.

Evidence: the reviewed plan requires the excluded worker proposal path to exercise
“one real worker proposal route that files its canonical proposal/recap”. The production
route that does that is `POST /tickets/{ticket_id}/propose`, which calls
`file_current_proposal_with_recap`. The readiness exclusion test only posts to
`/api/tickets/{ticket_id}/propose/success`, the field-only proposal route.

Violated contract: implementation prompt item 10 and plan section “Every intentionally
non-ringing path” require every explicit successful non-ringing path to be exercised.

Fix: add a zero-ring assertion for `POST /api/tickets/{ticket_id}/propose` with both
`body` and `recap`, asserting the proposal and recap persist and the recording doorbell
count stays unchanged.
```

Disposition: accepted. The omitted public route is a distinct canonical write and is
part of the reviewed negative matrix. Resolution and follow-up output are added after
the corrected diff is reviewed.

The first follow-up started before the final test-oracle repair was staged. Its full
output correctly identified that stale index state:

```text
1. Severity: Medium — the staged Chief exact-replay test is stricter than the reviewed
contract and can fail on a one-second boundary.

Evidence: the staged index copy snapshots `SELECT *` and compares the full ticket tuple
after exact replays. But the reviewed plan says Chief reconcile ignores only
`updated_at`, and production exact replay still refreshes it.

Fix: stage the existing unstaged test/report repair: compare all ticket columns except
`updated_at`, assert the event sequence is unchanged, and separately prove `updated_at`
refreshes under a fixed `TestClock`.
```

Disposition: accepted and already corrected before this output arrived. The repair is
now staged. It advances a deterministic clock, separately proves the timestamp refresh,
and compares every other Ticket column plus the complete event sequence. A final review
of that actual staged state follows.

Full final follow-up output:

```text
NO VIOLATIONS
```
