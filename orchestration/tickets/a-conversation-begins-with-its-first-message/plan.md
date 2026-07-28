# Plan

Revised after independent review. The review's findings are folded in; where a ruling went
against the review it says so and why.

## What exists now

- `POST /api/tickets/{ticket_id}/conversation` (`tickets/api.py:978`) creates a Ticket's
  conversation from `worker_resolve(conn, ticket)` with no overrides, and links it. Its
  `if ticket.conversation_id is None` is the only thing today stopping two creates.
- `POST /api/chief/conversation` (`tickets/api.py:948`) does the same through `agent_resolve`.
- `POST /api/conversations/{conversation_id}/send` (`conversation/api.py:261`) is the generic
  send and knows nothing about Tickets or the Chief.
- `LiveConversation.svelte` takes `onStartConversation`, calls it when it has no id, then
  `sendPrompt(id, sendBodyFor({message, current, picked}))`. `picked` becomes
  `model_change` / `reasoning_effort_change` through `armedChangeFor` — and is null until
  somebody touches a picker, while the face shows the backend default.
- `worker_step_readiness_loop.py:104` does the same two steps, with
  `worker_context_service.prepare` sitting between them.
- `worker_resolve` / `agent_resolve` already take `ConversationStartOverrides`. Nothing
  passes one.
- `reset_ticket_conversation` / `reset_agent_conversation` kill, unlink and create nothing.
  They are already right. New is already right. Neither is touched.

## The shape

One send door per owner, with the conversation id in the body.

```
POST /api/tickets/{ticket_id}/conversation/send
POST /api/chief/conversation/send
```

Body: today's `SendBody` fields, plus

- `conversation_id: str | None` — absent means the sender has none and this message is to
  create one.
- `backend_key: ConversationBackendKey | None` — used only when creating, through
  `require_conversation_backend_key`, which is the stated single door for untrusted backend
  text.

When creating, `model_change` and `reasoning_effort_change` are not changes: they are what
the conversation is created on, passed as `ConversationStartOverrides`. The delivery that
follows carries no change, because those values are already in force.

Reply: `{"conversation_id": str | None, "fate": ...}`. Null when a create did not land, so
the browser has nothing to adopt.

**A named conversation must be the one its owner currently points at.** A send naming
anything else is refused. Reset kills but leaves the record, and `system.send` will happily
pick an orphan back up — a second tab that missed a reset would otherwise get a live agent
nothing points at.

## Steps

**1. One writer** — `runtime/conversation_start.py`

`send_to_ticket_conversation` gains `conversation_id: str | None` and `overrides`. No second
function: two names a preposition apart is what the naming rule exists to prevent.

- Id given: it must equal the Ticket's current `conversation_id`, else `PlannerError`.
  Then today's behaviour unchanged, including last-chosen columns written only on
  `PromptDeliveryStarted` and only against the conversation the send went into.
- Id absent: resolve with `worker_resolve(conn, ticket, overrides)`, create, link, deliver
  with no change carried.
  - The link is conditional — `WHERE conversation_id IS NULL`. A create that loses the race
    delivers into the winner and returns the winner's id; the reply already carries the id
    back, so the browser lands where everyone else is.
  - The undo is a `try/except` around create-and-deliver, not a branch on the fate.
    `system.send` **raises** for empty content, for a steer carrying a change, and for
    anything the adapter throws that is not a named failure — each of which would otherwise
    leave a linked conversation with no prompt row.
  - The undo names the id it created: `system.kill(new_id)` and
    `clear_ticket_conversation_link(..., expected_conversation_id=new_id)`. Not
    `reset_ticket_conversation`, which re-reads the Ticket and would kill whatever it is
    pointing at by then.
- A steer with no conversation id is refused at the door rather than creating a conversation
  in order to refuse a steer into it.

Same for the Chief over `agent_resolve`, with an equivalent conditional link and unlink.

**2. Killing a conversation stops its child** — `conversation/system.py`

`kill` returns early when no turn is running and never touches `state.child`
(`system.py:349-380`). So the undo above would leave a live claude process for the 30
minutes until the idle sweep. A killed conversation has no child by definition; this is
one line and it is the difference between the undo working and the undo leaking.

**3. The routes** — `tickets/api.py`

Add the two send routes. They keep `require_direct_write` and do the file-keeping the
generic send does (`_kept_message_content`) before anything is created, so a message whose
files will not store never makes a conversation. Delete `POST /api/tickets/{id}/conversation`
and `POST /api/chief/conversation`. `GET /api/chief/conversation` stays — reading which
conversation the Chief is in is not the same act as making one. Reset routes unchanged.

**4. The readiness loop** — `runtime/worker_step_readiness_loop.py`

One call instead of two, with `worker_context_service.prepare` moved **before** it: a
prepare that throws must not leave a conversation behind. Claim logic unchanged — started
and queued are success, only a refusal releases the claim — and a refused first message now
also leaves the Ticket unlinked, which is what the next pass wants to find.

**5. The browser** — `LiveConversation.svelte` and its three callers

- `onStartConversation` becomes `sendMessage(body) => Promise<{conversation_id, fate}>`.
  The component stops knowing how a conversation comes to exist; it hands over what was
  typed and is told which conversation it went into. It adopts the id only when one comes
  back.
- `TicketRoute` and `ChiefConversation` implement it against their new routes.
- `DevConversationRoute` keeps the generic create — that is what the dev tool is for — and
  implements `sendMessage` as create-then-send itself.
- **The composer shows what a create would use.** Today the face is the backend default
  while a create would resolve to the Ticket's last-chosen values, so the contract's "on
  exactly the values the composer is showing" is not true. `TicketRoute` already holds
  `employee_backend` / `employee_launch_model` / `employee_launch_reasoning_effort` and
  passes them as the composer's starting values; the Chief passes its managed defaults.

**6. Repairing what is already stuck** — new alembic revision

A conversation with no prompt row has never been spoken into, so under this rule it does
not exist. Unlink every Ticket and agent pointing at one. The two on the VPS then read as
Tickets with no conversation — the state New leaves, and the state the send door knows how
to answer. The rows stay; nothing points at them and nothing will resume them.

Clearing the phantom cursor instead was the wrong call and is dropped: keeping the link
means the next message still comes from a composer with a picked model against a record
whose model is empty, which arms a change, rebinds, and mints a fresh phantom. Unlinking is
what makes the next message a create, and a create is the only shape that carries no change.

Chained on `conversation_available_commands`. `HEAD_REVISION` is pinned in
`tests/unit/test_db.py:30`, `tests/unit/test_db_ticket_status_changed_at.py:28` and
`tests/unit/test_conversation_migration.py:20`.

This also unlinks conversations opened by hand through the old start door and never spoken
in. Under the new rule that is correct rather than incidental.

**7. Documentation**

`docs/worker-orchestration.md:62-96` describes "finds the Ticket's conversation, or starts
one if it has none" as the flow. `docs/conversation-system.md` describes the doors. Three
docstrings state the two-door design as fact: `conversation_start.py:1-14`,
`tickets/api.py:953-996`, and `LiveConversation.svelte:13-15`.

## Ruled out of this ticket

**The dev screen still reproduces the original failure.** Its create form and the composer's
picker are two different controls, so picking a different model there arms a change on a
conversation that has never had a turn, which rebinds and resumes a session claude never
wrote. That is a second defect — *a conversation records a session before anything is said
in it* — and it is now reachable only from that screen. It gets its own ticket rather than
being folded in here, because it is a different rule from the one this ticket implements.

`_effort()` also still raises `SessionLoadFailed` for a reasoning effort outside claude's
five (`claude_agent_sdk.py:563-576`), so "the backend's session would not load" is no longer
uniquely diagnostic of the bug this fixes.

## Gates

- `tests/unit/test_conversation_start_wiring.py` — the writer being changed is this file's
  whole subject, and the natural home for the undo and race tests.
- `tests/unit/test_ticket_conversation_routes.py`, `tests/unit/test_chief_conversation_routes.py`
  — rewritten against the send door.
- `tests/unit/test_worker_step_readiness_loop.py`, `tests/unit/test_conversation_system.py`
  (kill stopping its child).
- `tests/unit/test_conversation_migration.py`, `tests/unit/test_db.py`,
  `tests/unit/test_db_ticket_status_changed_at.py`.
- `tests/e2e/test_conversation_three_states.py:224` and
  `tests/e2e/test_ticket_conversation_reply.py:65` both seed through the deleted route;
  the latter's scripted send replies become `{conversation_id, fate}`.
- `web/tests/production-surfaces.test.mjs:55` asserts the deleted route strings;
  `web/tests/conversation-wire.test.mjs:94-99, 572-620` covers `sendBodyFor`/`armedChangeFor`.
- One full `./verify` on the settled tree.
- Then the real proof: deploy, and send a first message in a new claude conversation on the
  VPS with a model and effort chosen in the composer.
