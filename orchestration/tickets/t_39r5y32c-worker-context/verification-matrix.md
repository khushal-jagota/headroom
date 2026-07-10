# Verification matrix

## Worker-context subsystem

- Same worker/key upserts one row and increments revision.
- Different keys coexist and render deterministically.
- Exact-revision acknowledgement deletes only what was sent.
- A newer revision written after snapshot survives acknowledgement.
- Ticket deletion removes pending context.

## Ticket-change producers

- Human edited approval sets `ticket_changed`; unedited approval does not.
- Every gated field uses the same acceptance path.
- Human direct settled-value, scope, recap, field-note, and Ticket PATCH edits set it.
- Agent priority/deadline/sprint, recap, field-note, and proposal writes do not set it.
- Many human edits before delivery still leave one `ticket_changed` slot.

## Hermes delivery

- Normal and already-claimed/return-for-revision System B worker steps include context.
- Sync chat send includes context without altering visible Panels text.
- Streaming/background human turn includes context without altering visible Panels text.
- Sync and streaming model-backed command prompts include context.
- Display/non-model commands leave context pending.
- Both `_submit_and_drain` and `_stream_prompt` acknowledge only after successful `prompt.submit`.
- Busy, RPC error, and pre-submit failure retain context.

## Repository gates

- Focused worker-context, ticket-engine/API, gateway, command, System B, and browser tests pass.
- Codex implementation review has no unresolved violations.
- One final integrated `./verify` passes.
