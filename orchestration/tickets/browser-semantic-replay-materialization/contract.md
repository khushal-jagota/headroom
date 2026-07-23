# Contract: replay semantic conversation state instead of streaming transport fragments

## Observed failure

The current ready-stream replay buffer retains every validated ACP notification exactly as it was
published live. On Ticket `t_b5ja4rqu`, 4,713 Codex `agent_message_chunk` notifications carry only
24,860 bytes of text but consume about 1.19 MiB once each fragment is wrapped in a browser envelope.
Command output is also retained once as terminal deltas and again in the completed tool update's
`rawOutput`. The resulting snapshot crosses the 1 MiB integrity limit and every browser attach closes
with 1013 `conversation replay unavailable; retry`.

This is a shared replay-representation defect. It is not a frontend connection defect and must not be
implemented as a Codex-only workaround.

## Required behavior

1. Live delivery is unchanged. Every attached browser receives every validated notification in its
   original order and with its original canonical sequence.
2. The Hub owns one backend-independent browser replay materializer used for Hermes, Claude, and
   Codex streams. Backend definitions do not select replay retention policy.
3. Consecutive ACP user-message, agent-message, or agent-thought text chunks with the same complete
   non-text shape occupy one replay slot. Their text is concatenated in wire order. The complete shape
   includes session update kind, optional message id, and all notification, update, and content
   metadata; a shape change or an intervening envelope starts a new slot.
4. Non-text content, malformed updates, and unrelated notifications remain ordinary typed replay.
   Coalescing never crosses an intervening tool, plan, activity, receipt, permission, or other envelope.
5. An exact active `tool_call_update` terminal-output delta is delivered live but not retained in
   replay. It must contain no status and only the matching `terminal_output_delta` extension with
   string data and a non-empty `terminal_id` equal to `toolCallId`. Ambiguous or additional fields
   remain ordinary replay.
6. Tool start/status/content updates and completed or failed tool updates remain replayable. Reconnect
   omits `rawOutput` and embedded `terminal_output` / `terminal_output_delta` metadata from every
   completed or failed tool update because the browser progress view presents none of those fields.
   Tool identity, title, kind, status, content, locations, terminal exit, and unrelated provider
   metadata remain typed and replayable.
7. Reconnect snapshots remain typed, bounded to 1 MiB, subscriber-local, and resequenced into one
   contiguous history ending at the canonical stream sequence. Materialization does not mutate the
   canonical sequence, call `session/load`, or publish anything to existing browsers.
8. Attempted live envelope count and bytes remain diagnostic facts. Retained-byte accounting reflects
   only current semantic replay entries. A genuinely oversized semantic snapshot still fails closed.

## Acceptance gates

- Unit tests prove contiguous message/thought concatenation, metadata and boundary isolation,
  non-text fallback, exact terminal-delta omission, and malformed terminal fallback.
- A focused Hub regression exceeds a low replay limit with raw message and terminal fragments, proves
  exact live delivery, then proves reconnect receives coalesced text plus compact final tool status
  without `rawOutput`, embedded terminal output, or another backend attach.
- Focused Hub snapshot integrity and slow-browser tests remain green.
- One canonical `./verify` passes on the settled tree.

## Owned files

- `src/planner/conversation/hub.py`
- `src/planner/conversation/backend_contracts.py`
- one shared browser replay-materializer module
- backend definition cleanup only where the old Codex-specific policy was selected
- focused unit tests, `docs/chat.md`, `PROGRESS.md`, and `decisions.md`
