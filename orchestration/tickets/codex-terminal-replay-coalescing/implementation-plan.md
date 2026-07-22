# Implementation plan

1. Add a typed backend replay-materialization contract in `backend_contracts.py`:
   `SessionNotificationReplaySlotAdmission(slot_key, disposition)` plus a
   `SessionNotificationReplayMaterializer` protocol with `classify(notification)` and
   `materialize(notifications)`. `AgentBackendDefinition` gains an optional materializer; `None`
   retains exact append-only replay. The Hub sees only generic slot keys, dispositions, and typed
   notifications.
2. Implement and register a Codex-owned materializer. It recognizes only exact Codex 1.1.4 active
   delta and terminal aggregate shapes frozen in the contract. Active deltas accumulate by session
   and tool/terminal id; exact terminal updates replace. Materialization concatenates active data
   once or returns the authoritative final notification, including an exact final shape whose
   `terminal_output_delta.data` is the empty string. Every malformed, mixed, foreign, or final
   shape missing `terminal_output_delta` remains append-only.
3. Replace the Hub's serialized replay list with ordered generic entries: ordinary validated
   envelopes and backend-owned materialization slots. Immediate browser serialization/fan-out is
   unchanged. A slot moves to its newest admitted canonical sequence; replacement discards prior
   fragments. Reset clears entries and indexes. Provider slots materialize only when a subscriber
   snapshot is requested, avoiding quadratic concatenation.
4. Validate the materialized subscriber snapshot: one leading reset, exact stream identities,
   strictly increasing canonical ordinals not beyond the stream, and at least one ready. Preserve
   non-coalesced relative order, move the latest ready marker last as today, then resequence the
   subscriber-local copy contiguously to end at the canonical stream sequence. Do not mutate or
   republish the canonical stream and do not call registry attach/session load.
5. Keep diagnostics and limits truthful. Original live-envelope count/attempted bytes still count
   every admission. Replay availability and stored bytes use the validated materialized snapshot.
   The existing 1 MiB ceiling and 1013 failure remain for genuinely oversized materialized state;
   slow-browser live queues are unchanged.
6. Add RED-first focused tests: Codex shape/merge unit tests; a Hub overflow regression with an
   existing browser receiving every original update and a cursor-carrying reconnect receiving one
   consolidated slot; interleaving/two-terminal latest-position behavior; completion replacement;
   malformed fallback; byte replacement accounting; genuine aggregate overflow; contiguous
   sequence handoff; and a real WebSocket cursor case if the Hub seam does not cover it. Retain all
   existing atomic-load, integrity, overflow, and slow-browser assertions.
7. Update `docs/chat.md`, `PROGRESS.md`, and `decisions.md`; run focused pytest, Ruff, strict Mypy,
   independent implementation review, one canonical `./verify`, commit, restart, and prove
   `t_xq6ragj3` reaches ready without sending a message.
