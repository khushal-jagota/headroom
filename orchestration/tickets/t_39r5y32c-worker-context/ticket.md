# Ticket t_39r5y32c — Worker awareness of user edits

## Outcome

Workers receive one coalesced `ticket_changed` context item after relevant human Ticket or Review edits. That context reaches the actual Hermes session with whichever model-bound interaction is submitted next.

## Constraints

- Pending worker context is a first-class keyed subsystem, not ticket-specific state or Panels chat.
- Repeated edits coalesce by key.
- Future context keys do not require gateway changes.
- Only exact delivered key revisions are acknowledged after Hermes accepts `prompt.submit`.
- Agent-authored proposals, recaps, and notes plus unedited approvals do not mark the ticket changed.
