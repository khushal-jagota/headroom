# Live chat accuracy / server-owned live turn state

## Product invariant

The chat panel renders one server-owned `ChatState` resource. The resource contains durable visible
messages plus the optional active turn. Streaming and polling are delivery mechanisms for that same
state; neither the browser nor Hermes history is the product source of truth.

## Scope

- Ticket chat, automatic worker steps, and Chief of Staff chat use the same chat-state contract.
- Hermes remains the transport and durable session owner.
- Panels owns visible chat messages, active-turn status, phase, partial output, activity labels,
  errors, and event invalidation.

## Implementation shape

- Add `chat_messages` for product-visible transcript rows.
- Add `chat_turns` for live/completed turns, with a partial unique index enforcing one running turn
  per entity.
- Add chat lifecycle event kinds: `chat_message_recorded`, `chat_turn_started`,
  `chat_turn_updated`, and `chat_turn_finished`.
- Add `GET /api/chat/{entity_id}/state`.
- Add `POST /api/chat/{entity_id}/turns`, which starts a background server-owned human turn.
- Wire System B to create/update/finish worker turns through the same writer.
- Switch `ChatPanel` from `/history` plus local streaming transcript to `/state` plus active-turn
  polling while the server says a turn is running.

## Tests

- Unit tests cover human turn persistence, active turn visibility, worker turn recording, schema,
  and event mapping.
- E2E checks cover ticket chat and Chief of Staff sends across navigation/remount by asserting
  `/api/chat/{entity_id}/state` before the panel is remounted.
- E2E checks cover active worker state by seeding a running server-owned worker turn in SQLite and
  verifying the chat panel renders the worker activity and pending indicator after remount.
