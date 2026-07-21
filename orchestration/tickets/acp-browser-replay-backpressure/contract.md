# ACP browser replay and live backpressure

## Problem

Panels currently puts a complete ACP conversation replay into the same bounded queue used to
protect live delivery. The WebSocket writer starts only after attachment completes. A replay with
128 or more envelopes therefore fills the production queue before the browser can receive its
first envelope. Panels closes the socket with `conversation client is too slow`, the browser retries,
and the transcript remains empty even though the employee continues working.

## Required behavior

1. A fresh `/api/conversation` WebSocket attachment must receive every valid replay envelope in
   order even when the replay contains more envelopes than the live browser queue can hold.
2. Updates produced while replay is being delivered must follow that replay without gaps,
   duplication, reordering, or mixed binding generations.
3. Initial replay must not use the bounded live-delivery queue as pre-writer storage. Replay and
   live backpressure are separate phases of one ordered subscription.
   This applies to every reset-producing path, including first load, idle refresh, active attach,
   compaction, and binding replacement. An already-connected browser must never receive historical
   reset replay through its bounded live queue; it must receive a complete subscriber-local replay
   bootstrap or be closed before any partial replacement replay is exposed.
4. The bounded live queue remains. A browser that genuinely stops consuming after it is attached
   is evicted without blocking the ACP child, employee turn, or another healthy browser.
5. Raise the production live browser queue capacity from 128 to 1,024 envelopes. This is operational
   headroom only; replay correctness must be proven with a replay larger than the configured test
   capacity.
6. A replay that cannot be supplied for a real replay-integrity reason fails before a partial
   transcript is presented and uses the existing replay-unavailable close reason.
7. Every server-initiated conversation closure caused by replay unavailability or live slow-client
   eviction emits a structured, actionable log record. It must identify the employee, ACP session,
   binding generation, close reason, relevant envelope/byte counts and limits, and connection id
   when one exists. Logs must not include prompt or transcript content.
   Each such closure also releases its permission-browser attachment exactly once, even when the
   WebSocket finalizer races the server-initiated close path.
8. Existing cursor, compaction, replacement-generation, permission, terminal, and shutdown behavior
   remains unchanged.

## Acceptance seams

- **Real WebSocket replay seam:** an ACP replay larger than the live queue capacity attaches through
  `/api/conversation`, delivers the complete ordered transcript, reaches ready/idle, and then delivers
  one new live update in sequence.
- **Live slow-consumer seam:** a deliberately non-consuming attached browser fills only its live
  queue, is evicted with the existing close reason, and does not impede a healthy browser.
- **Operational logging seam:** replay-unavailable and live slow-consumer closures produce the exact
  contextual warning records without conversation content.
- **Configuration seam:** production composition uses 1,024; focused tests may inject smaller limits.
- **Reset-transition seam:** an existing healthy WebSocket crossing a reset-producing refresh or
  replacement never receives historical replay through its live queue and never sees a partial
  replacement transcript.

## Allowed files

- `src/planner/conversation/hub.py`
- `src/planner/conversation/configuration.py`
- `src/planner/conversation/composition.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py`
- `docs/chat.md`
- `PROGRESS.md`
- `decisions.md`

## Gates

Run focused Ruff and strict Mypy for changed Python files, the focused hub tests, the focused real
WebSocket replay tests, and then one final repository `./verify` after integration.
