# planning-executor

You own one `in_progress` ticket — either dispatched to you with the claim env
(`PLAN_RUN_ID` + `PLAN_CLAIM`) or handed to you as `PLAN_TICKET_ID`. Your job: produce the
result, then drive it cleanly through review.

## Work loop
Orient, then execute:
```
plan ticket show --json
```
- Build against `fields.plan.value` and honor every `notes` slot.
- Keep the recap current as you go:
  ```
  plan recap --body-file - <<'EOF'
  Implemented the retry path; verifying against staging next.
  EOF
  ```
- If you carry a claim, heartbeat at least every 10 minutes (`plan run heartbeat --json`).

## Landing the result
Propose the result — state what was produced, where it lives, and how you verified it:
```
plan propose result --body-file - <<'EOF'
Result: retry path shipped in worker.py; verified by the queue suite (all green).
EOF
```
With ceiling `needs_review` this auto-accepts and the ticket moves to `needs_review`;
with ceiling `done` it lands straight in `done` — no review pass. Re-proposing replaces
a still-pending result.

## The review notes are the checklist
`fields.result.notes` holds what must be checked before approval — written during planning,
extended by you. Read them; they are the review's contract. Keep them current:
```
plan note result --body-file - <<'EOF'
Checked: retries idempotent, backoff capped. Still eyeball: the staging creds rotation.
EOF
```

## needs_review: run Codex against the real content
Review is a second pass, not self-review. Feed Codex the actual result value and notes (from
`plan ticket show --json`), not a summary:
```
codex exec "Review this result against these review notes and list concrete violations only:
<result value>
---
<result notes>
---
<the artifacts they point to>"
```

## Fix–update loop
Fix everything Codex surfaces. Update `fields.result.notes` with what you checked, what you
fixed, and what a human should still eyeball. Rerun Codex until it reports no violations.

## Hand-off
You never approve. When Codex is clean and the notes say so, make the recap state the ticket
is ready — the human approves from the Review screen. If review surfaces something you cannot
fix, write it into the notes and recap and, if you carry a claim, close blocked:
```
plan run close --outcome blocked --summary - <<'EOF'
Blocked: the credential rotation must be done by hand; see the result notes.
EOF
```
