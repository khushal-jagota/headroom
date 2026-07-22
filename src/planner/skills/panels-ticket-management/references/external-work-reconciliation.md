# External-work reconciliation notes

Use this when the user reports that work completed outside the Panels worker flow should be recorded on an existing or new ticket.

## Preferred path

Use the explicit Chief external-work commands when the running server supports them. Current completed tickets have five settled fields: `success`, `approach`, `plan`, `implementation`, and `closeout`.

```sh
panels chief create-ticket-from-external-work \
  --title "..." \
  --state done \
  --user-note-file note.md \
  --recap-file recap.md \
  --success-file success.md \
  --approach-file approach.md \
  --plan-file plan.md \
  --implementation-file implementation.md \
  --closeout-file closeout.md \
  --priority P2 \
  --json
```

For an existing ticket, use `panels chief reconcile-ticket-from-external-work <ticket_id>` with the same field files and `--state done`.

Keep the fields light when the user only wants an already-finished item recorded. The user's direct completion report is valid reconciliation evidence, but attribute it explicitly; do not invent tests, commits, deployment evidence, or implementation detail they did not provide.

After success, read the ticket back and verify `state`, `ticket_status`, settled fields, recap, and day placement. Add newly reported completed work to today unless the user says backlog/later. Preserve an existing day placement rather than adding it redundantly.

## Existing ticket with active control or a pending proposal

Chief reconciliation deliberately rejects both active ticket control and pending proposals. Do not work around this with direct database edits.

1. Read the ticket and chat state. If a chat/worker turn is genuinely running, do not interrupt it merely to reconcile; settle or stop that turn through the normal UI/runtime path first.
2. If the ticket is only parked at `ticket_status: awaiting_approval` with no running turn, clear the stale control through the normal release endpoint.
3. If the current gated field still has a pending proposal and the user has explicitly said the work is complete, settle that proposal before reconciliation:
   ```sh
   panels ticket approve <ticket_id> \
     --ceiling <new_state_after_approval> \
     --at-cap stop \
     --json
   ```
   The ceiling must be at or beyond the state produced by approval. Using that exact next state with `at_cap=stop` prevents the readiness loop from dispatching another worker while the Chief reconciliation is prepared.
4. Run the Chief reconciliation to `done`, then verify the ticket reports `state: done`, `ceiling: done`, `at_cap: stop`, and `ticket_status: empty`.

Do not silently accept a proposal that contradicts the user's completion report. In that case, return/revise it through the ordinary approval workflow or ask for the missing decision.

## Releasing an errored ticket to resume work

A release rings the readiness doorbell. If the ticket is on today, has no pending proposal, and is runnable, it may move immediately from `errored` through `empty` to `agent_running_step`. Before release, verify there is no live Hermes worker turn for the ticket's session key. After release, report the observed post-release status rather than promising it will remain `empty`.

For external reconciliation specifically, `ticket_status: errored` is accepted by the Chief reconciliation path and is cleared after the canonical fields/state are settled, provided no worker/chat turn is active.

## Fallback when the Chief API route is unavailable

If the CLI has `panels chief ...` but the running server returns HTTP 404 for `/api/chief/...`, the server is likely older than the CLI/code. Do not split the work or change its meaning. Create exactly one ordinary ticket and advance the five gated fields through proposal/approval:

1. Create the ticket with only the known parent scope. When `--sprint-item` is supplied, do not redundantly pass `--sprint` or `--project`; they are derived.
2. For each field, propose it and approve it with the next lifecycle state as the ceiling:
   - Success → `needs_approach`
   - Approach → `needs_plan`
   - Plan → `needs_implementation`
   - Implementation → `needs_closeout`
   - Closeout → `done`
3. Use `--at-cap propose` for intermediate approvals and `--at-cap stop` for the final Closeout approval.
4. Read the ticket back after every approval; do not infer the resulting state from the CLI verb alone.
5. Check for an existing matching ticket before creating a fallback ticket.

## Capture style

- Preserve the singular shape the user asked for, even when the work contains several connected changes.
- Preserve the user's wording lightly in `user_note`; use concise decision-level text for the five canonical fields.
- For a simple “this is already done” correction, a short recap such as `Done: <observable outcome>.` is enough.
- State when completion is based on the user's report. Do not manufacture stronger evidence.
- Report any fallback clearly, but do not let it alter the requested reconciliation shape.
