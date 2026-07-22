# Contract: keep live terminal streams from poisoning reconnect replay

## Observed failure

Ticket `t_xq6ragj3` loads a valid Codex session and continues running, but opening its Chat closes
with WebSocket 1013 `conversation replay unavailable; retry`. Two verification turns emitted
4,140 `terminal_output_delta` updates. Their 535,573 bytes of terminal text become thousands of
browser envelopes, pushing the ready stream's append-only reset buffer over its 1,048,576-byte
integrity limit. A fresh private `session/load` is only 590 notifications / 361,465 bytes because
the adapter represents completed commands compactly.

Atomic ACP load is already landed. This defect occurs after `ready`, while live updates accumulate.

## Required behavior

1. Every currently attached browser continues to receive each validated live envelope immediately,
   in its original order and with its original sequence. Slow-browser isolation is unchanged.
2. Provider metadata remains behind the backend boundary. `AgentBackendDefinition` may supply one
   typed, default-no-op replay materializer. The Hub handles only opaque materialization keys/state
   and generic replay slots; a Codex-owned implementation alone recognizes and combines Codex
   1.1.4 terminal extensions. No external adapter package is changed.
3. The ready-stream reconnect snapshot is a distinct materialized representation. Repeated Codex
   `tool_call_update` notifications whose `_meta.terminal_output_delta` is exactly a mapping with
   string `data` and non-empty string `terminal_id == toolCallId`, for the envelope's ACP session,
   occupy one replay slot keyed by session and tool-call identity.
4. Before a terminal status, that replay slot represents the ordered concatenation of admitted
   `data` fragments. An exact `completed` or `failed` update containing both the matching
   `terminal_output_delta` and a matching `terminal_exit.terminal_id` replaces the accumulated
   delta with the adapter's final aggregate while preserving status, `rawOutput`, `terminal_exit`,
   and every other field on that terminal update. A final update with only `terminal_exit` remains
   append-only; it does not ambiguously clear prior output.
5. The consolidated slot moves to the newest admitted delta's temporal position. In an interleave
   `tool-1 delta A -> unrelated X -> tool-1 delta B`, replay order is `X -> combined tool-1 AB`.
   All non-coalesced envelopes retain their relative order, including simultaneous terminal IDs.
6. Coalescing is extension-shape-specific and fail-closed: malformed metadata, unknown extra
   metadata on an active delta, mismatched terminal identifiers, non-string data, `terminal_output`
   snapshots, mixed update kinds, and every unrelated envelope retain append-only behavior and
   cannot corrupt an existing materializer.
7. A reconnect snapshot is validated and resequenced into one contiguous subscriber-local history
   ending at the canonical stream sequence. It does not mutate the canonical stream, call
   `session/load`, or publish anything to existing browsers.
8. A reconnect carrying the browser's real last-seen binding generation and sequence receives the
   same complete snapshot without a future-cursor rejection. Its next live envelope is exactly the
   final subscriber-local replay sequence plus one.
9. The 1 MiB replay-byte ceiling remains the final integrity guard. Availability and stored-byte
   accounting use the current materialized slots, subtracting a replaced slot before adding its
   successor. Attempted live-envelope count/bytes remain diagnostic facts. One genuinely oversized
   materialized snapshot still fails closed with the existing 1013 reason.
10. No external adapter dependency, durable session, Ticket state, or frontend rendering contract
    changes.

## Acceptance gates

- A focused Hub regression publishes enough exact Codex 1.1.4-shaped small terminal deltas to
  exceed 1 MiB under the old
  append-only representation, proves the already attached browser receives every original update,
  then reconnects a second browser with the first browser's real cursor and proves it receives
  reset, one truthfully consolidated terminal update, ready, and a contiguous handoff to the next
  live sequence without another registry attach/load.
- Focused tests cover active-terminal concatenation, terminal completion replacement, malformed or
  conflicting metadata falling back unchanged, interleaving/two-terminal order, materialized byte
  replacement accounting, genuine post-materialization overflow, and existing snapshot integrity /
  slow-browser behavior.
- The original live WebSocket attach for `t_xq6ragj3` reaches `ready` after restart.
- One canonical `./verify` passes on the settled tree.

## Owned files

- `src/planner/conversation/hub.py`
- `src/planner/conversation/backend_contracts.py`
- one Codex-owned replay-materializer module and `src/planner/conversation/codex_backend.py`
- `tests/unit/test_conversation_hub.py`
- focused unit coverage for the Codex replay materializer
- only if required for the real WebSocket seam: `tests/e2e/test_acp_conversation.py`
- `docs/chat.md`, `PROGRESS.md`, and `decisions.md`
