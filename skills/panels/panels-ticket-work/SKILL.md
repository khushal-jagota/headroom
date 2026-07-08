---
name: panels-ticket-work
description: Working Panels tickets through their gated fields, proposal flow, and approval/scope constraints.
---

# Panels ticket work

Use this when the user asks you to work a Panels ticket or propose a ticket field (`success`, `approach`, `plan`, or `result`). A ticket is worked one gate at a time: inspect the ticket, gather the narrow context needed for the current field, propose only that field, and verify the ticket state after the proposal.

## Standard flow

1. Read the current ticket and its field notes with `panels ticket show <id>`.
2. Ground the proposal in the repository/docs/ticket context before writing it.
3. Propose the requested field with `panels propose <field> <id> --body-file -`.
4. Verify the resulting ticket state and field slot, not just the command exit line.
5. Report the state succinctly: field proposed, current `state`, current `ticket_status`, and anything unusual.

## Scope pitfall: proposing at a stop ceiling

If `panels propose …` fails with `at_cap_stop`, the proposal body is not the problem. The ticket is at its current ceiling with `at_cap=stop`, so the worker is not allowed to park the next field.

When the user's explicit current instruction is to propose that field for approval:

1. Change only the scope needed to allow a parked proposal: keep the same ceiling and set `at_cap=propose`.
2. Use a human-authority surface for that scope change. The normal `panels` CLI sends an agent actor header by default, so human-only routes such as `/api/tickets/{id}/scope` must be called without `X-Plan-Actor` unless a human-mode CLI exists.
3. Immediately file the requested proposal.
4. Verify the ticket is `ticket_status=awaiting_approval` and the intended field has a pending proposal.

Do not broaden the ceiling, auto-advance the ticket, or make unrelated ticket changes just to get past `at_cap_stop`.

## Proposal quality

- Keep proposals lean and actionable.
- Name the concrete route through the work, not generic process language.
- Separate hard facts from assumptions and owner decisions.
- For implementation/result tickets, include evidence from real commands or artifacts.
