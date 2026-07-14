# AD05 — Canonical human Chat ingress

## Objective

Make the existing server-owned Chat turn the only way a human message or command enters a Panels Chat.
Delete the older request/response and SSE entry points instead of keeping compatibility wrappers. Preserve
the current visible Chat behavior, Hermes delivery, durable session handling, and exact `/new` transition.

This ticket is a deletion and concentration step. AD06 will deepen the internals of the surviving turn; AD05
must not redesign them first.

## Contracts implemented against

- `src/planner/chat/contracts.py`
- `src/planner/core/adapters/base.py`
- `web/src/lib/types.ts` (`StartChatTurnBody` remains the browser request contract)

The reviewed plan must freeze any declaration changes before implementation. The implementation agent does
not invent or alter those shapes.

## Required public boundary

Human Chat ingress remains:

- `POST /api/chat/{entity_id}/turns` for Ticket and any other supported chattable entity;
- `POST /api/messages/chief` as the deliberately narrow hosted Chief message shell over the same canonical
  human-turn service;
- the existing image-upload route followed by `image_references` on the canonical turn request.

The following routes are absent and return 404; no alias, redirect, or compatibility handler remains:

- `POST /api/chat/{entity_id}/send`;
- `POST /api/chat/{entity_id}/stream`;
- `POST /api/chat/{entity_id}/command`.

State, pause, history, status, image upload, and command-catalog reads are not Chat ingress and remain:
`/state`, `/pause`, `/history`, `/status`, `/images`, and `/chat/commands`.

## Required service and adapter boundary

`planner.chat.service.start_human_turn` is the sole human message/command admission service. The parallel
`service.send`, `service.stream`, and `service.run_command` functions are deleted.

The gateway adapter keeps one `stream(...)` transport because `_run_human_turn` consumes it for both
`mode="message"` and `mode="command"`. It also keeps status, history, interrupt, and catalogue reads. The
parallel synchronous `GatewayAdapter.send(...)` and `GatewayAdapter.run_command(...)` methods are deleted
from the protocol and every production, routing, offline, and test implementation.

Delete the legacy `ChatSendResult` and `CommandRunResult` contracts. Keep `ChatStreamChunk`, but name and
document it for the surviving gateway-to-canonical-turn stream rather than an SSE caller. Any internal
`/new` helper must return only what the surviving stream needs; it must not retain a deleted result shape.

Delete the unused browser `streamChat`/SSE parser path. `startChatTurn` remains the only browser ingress.

## Behavior that must remain exact

- Message and command requests still validate the same `text`, `mode`, and `image_references` contract.
  Empty image-only requests remain allowed only when an image reference exists; images remain message-only.
- Admission remains direct-only and rejects a competing Ticket employee step with `already_running`.
- The chattable entity boundary remains Ticket, canonical planning day, and the configured top-level agent
  entities. The narrow Chief route still accepts exactly one nonblank `text` field.
- The durable Hermes session key is stored before prompt submission, rotation is persisted, and a lost
  first-write race adopts the stored winner. Panels visible rows remain a mirror of real gateway delivery,
  never delivery by themselves.
- A human/command/image turn still creates one Panels `chat_turns` row, streams safe activity and output into
  it, and settles exactly once. Pause and immediate follow-up behavior is unchanged.
- Commands and skills still use the current command catalogue and system-versus-assistant output roles.
- Typing exactly `/new` in command mode creates one fresh Hermes session, persists/binds its durable key,
  leaves the visible Panels transcript in place, emits the system line `New session started.`, and sends the
  next ordinary message through that new live session. `/new` with arguments remains an ordinary command.
- Employee-step delivery and return-for-revision delivery remain separate from human Chat admission and are
  not routed through a Panels transcript write.

## Acceptance tests

The plan must map and either migrate or delete the legacy-only cases in these files:

- `tests/unit/test_chat_seed.py`;
- `tests/unit/test_chat_commands.py`;
- `tests/unit/test_authctx_routes.py`;
- `tests/unit/test_worker_context.py`;
- `tests/unit/test_minds.py`;
- `tests/unit/test_automatic_employee_step_eligibility_actions.py`.

Keep a behavior proof only when it exercises the surviving `/turns` → `start_human_turn` → gateway
`stream` path. Do not mechanically rewrite duplicate tests just to preserve deleted route/service/adapter
interfaces.

Focused acceptance must prove:

1. all three retired routes are 404 and their service, adapter, result-contract, and browser helper names are
   absent from live source;
2. canonical message, display-command, model-backed skill, image, validation, authorization, missing entity,
   offline/busy, key persistence/rotation, and Panels state behavior remain covered;
3. exact `/new`, next-message live-handle reuse, and `/new`-with-arguments behavior run only through the
   surviving stream path;
4. the command catalogue, state/history reads, pause, worker turns, Chief message shell, and browser Chat
   flows remain green;
5. no test or documentation presents a deleted route or synchronous adapter method as live.

The affected browser flows in `tests/e2e/test_flows_a.py` and `tests/e2e/test_live_chat_state.py` must keep
their current visible meaning. Comments that still name `/command` are corrected to the canonical turn, not
used to justify an obsolete compatibility route.

## Documentation

Update `docs/chat.md`, `docs/systems.md`, and generated `docs/systems.html` so a smart non-engineer sees one
human Chat turn path. Keep the worker-chat delivery warning explicit. Historical orchestration notes are
history and are not rewritten unless a live static guard reads them.

## Plan requirements

The delegated plan must:

- inventory every production and test implementation/caller of the deleted routes, functions, adapter
  methods, and result types;
- distinguish the surviving gateway `stream` transport from the deleted HTTP SSE/service stream;
- state the exact replacement or deletion disposition for every affected legacy test;
- enumerate a bounded changed-path allowlist before implementation;
- add RED tests/static guards before deletion;
- run focused Python, typing, frontend, and browser checks, leaving the full `./verify` to the orchestrator
  after a clean independent diff review.

## Out of scope

- Refactoring `start_human_turn`, `_run_human_turn`, session ownership, transcript writers, observation
  handling, or settlement beyond the glue needed to delete parallel ingress; AD06 owns that concentration.
- Changing Panels Chat versus Employee session-history behavior; AD09 owns that final isolated change.
- Resource-catalogue or managed-Markdown work from AD07–AD08.
- Renaming the visible `agent` copy, command semantics, Chat UI, or route shape that survives.
- Any compatibility layer for a deleted interface.
