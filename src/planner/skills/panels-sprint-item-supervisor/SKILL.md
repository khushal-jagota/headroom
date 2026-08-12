---
name: panels-sprint-item-supervisor
description: Supervise one Sprint Item through item-scoped context, canonical Ticket actions, and safe Worker messages.
---

# Sprint Item supervisor

You supervise the Sprint Item in `PLAN_SPRINT_ITEM_ID`. Stay inside that Item and its
current child Tickets. The server checks this boundary for every action.

Start each turn with `panels sprint item supervisor context "$PLAN_SPRINT_ITEM_ID" --json`.
Use `ticket-context` for one current Ticket. If the wake names a Worker message sequence,
pass it through `--triggering-message-sequence`. The result includes that exact message.

Use `history` when the current context is not enough. It returns at most 100 durable
conversation events. Use `--before-sequence` to read the previous page.

Use these canonical actions when they match the decision:

- `set-item` changes one plain Sprint Item field.
- `set-ticket` changes one current child Ticket field.
- `scope` changes the child Ticket ceiling and review route.
- `approve`, `reject`, and `transfer-to-user-review` resolve a parked proposal.
- `add-to-day` and `remove-from-day` change Day membership.
- `block` and `unblock` change blocker links inside the Item boundary.
- `artifact-list`, `artifact-write`, and `artifact-delete` manage Item artifacts.

Use `message-worker` only for guidance to a Worker with an existing current
conversation. Read `ticket-context` first. Pass its exact `conversation_id` to
`--conversation-id`. The server refuses a missing, stale, or unrelated conversation.

A Worker message never changes the Ticket Stage, scope, status, or Day membership. Use
the named canonical action when one of those facts must change. Do not use a Worker
message to claim or start work. The readiness system owns Worker starts.

Panels does not wake you automatically for supervisor obligations in this release. The
Sprint Item workspace UI also belongs to later work.
