# ACP browser replay and live backpressure implementation plan

## Ordered delivery model

Each browser subscription has one writer and one ordered outbound stream, but replay bytes never occupy its bounded live queue:

- A fresh or active attach receives an immutable subscriber-local replay bootstrap, then live envelopes admitted after its sequencer-owned cutoff.
- A browser already connected when a reset occurs keeps its earlier live queue order. The queue receives one lightweight replay-cutover marker; the marker references an immutable subscriber-local bootstrap. The writer sends that complete bootstrap before consuming later live entries.
- Every reset epoch is constructed and byte-validated off-stream before either a fresh browser or an existing browser can see its reset envelope. An invalid epoch closes affected browsers with replay-unavailable; it never exposes a prefix.

The same reset helper must own first load, idle refresh, active attach snapshot, compaction replacement, requested-cancel runtime replacement, and New Conversation/binding replacement. Sequence allocation remains on the employee sequencer; wire contracts and cursor semantics do not change.

## Implementation

1. **Add focused red tests before changing the hub.**
   - In `tests/unit/test_conversation_hub.py`, add `test_active_turn_attach_orders_replay_larger_than_live_queue_before_ready_and_live` and `test_cursored_active_turn_attach_preserves_generation_and_sequence_handoff`. Use an injected small queue, assert replay is subscriber-local, and prove exact sequence/generation order through the handoff `ready` and one live update.
   - Add `test_connected_browser_crosses_large_reset_without_replay_entering_live_queue` covering an already-connected healthy browser across the shared reset helper. Assert the queue contains one cutover marker rather than historical envelopes, the writer emits the complete reset/replay/ready epoch in order, and later live delivery follows it. Exercise idle refresh and parameterize the same assertion over compaction, requested-cancel recovery, and binding replacement so every reset-producing caller is pinned.
   - Add `test_connected_browser_replacement_replay_byte_failure_closes_before_reset_prefix` with a sentinel transcript value. Assert no replacement reset or replay reaches the socket, the old live prefix is not mixed with the replacement generation, and closure uses `REPLAY_UNAVAILABLE_CLOSE_REASON` with content-free count/limit logging.
   - Extend the real route coverage in `tests/e2e/test_acp_conversation.py` with `test_real_websocket_replay_larger_than_live_queue_reaches_ready_then_delivers_live_update` and `test_existing_websocket_crosses_large_replacement_replay_without_partial_transcript`. Both use replay larger than the injected queue but below the byte limit; the latter keeps the socket connected across a real reset transition and proves complete replacement replay plus the next live sequence.
   - Preserve the genuine overflow seam as `test_slow_live_browser_is_evicted_without_blocking_healthy_browser_and_logs_safe_context`: only live envelopes fill the queue, one browser closes, and the healthy browser and ACP publisher continue.

2. **Build and commit every reset epoch atomically in `src/planner/conversation/hub.py`.**
   - Introduce a private immutable replay-bootstrap/cutover value containing the serialized reset epoch, binding generation, first/last sequence, envelope count, and UTF-8 byte count. Keep `BrowserSubscription.queue` bounded and typed for live envelopes or the small cutover marker; no historical serialized envelope is inserted into it.
   - Refactor reset production into one sequenced operation that creates a candidate stream with no browsers, emits reset, classified replay, captured/quarantined ingress that belongs inside replay, and ready, and validates the entire candidate before replacing the live stream. Merge `_establish_stream`'s current separate bind/ready operations so no ingress can interleave between validation and commit.
   - On a valid commit, carry forward the exact browser set. For each existing browser, enqueue one cutover marker after its already-admitted live entries; then publish queued echoes, queue snapshots, and later updates behind that marker as ordinary live entries. If the marker itself cannot enter a full live queue, treat that browser as a genuine slow client. New/fresh browsers receive the same immutable epoch as their initial bootstrap without using the queue.
   - On an over-byte candidate, retain total attempted envelope/UTF-8-byte counts but retain no replay prefix. Commit the required runtime/binding transition without exposing the candidate, close every carried browser as replay-unavailable, and make later attach fail from the unavailable reset epoch until a valid reset replaces it. Existing workers and healthy ACP processing remain unblocked.
   - Route `_bind_stream`, compaction commit, requested-cancel recovery commit, and forced new-binding establishment through this helper. Keep same-binding sequence floors and replacement-generation sequence behavior exactly as today. Active attach snapshots the current valid epoch and registers the browser in the same sequenced operation; its handoff `ready` and all later publications enter live delivery after the snapshot cutoff.
   - Preserve cursors: both fields are still paired, mismatched generations and future sequences still fail, unloaded attach still raises the sequence floor, and same-generation active attach still supplies the complete reset epoch. The existing browser reducer remains responsible for ignoring already-seen sequence numbers; no delta-replay protocol is added.
   - Update `_websocket_writer` to drain initial bootstrap or a queued cutover bootstrap before selecting the next live item. Check the existing closed event between sends and preserve current cancellation/close behavior. A later reset marker naturally waits behind prior live entries, while entries admitted after reset wait behind its marker.

3. **Give permission cleanup one idempotent owner and prove exact-once races.**
   - Make each `BrowserSubscription` own a single permission-detach claim/task. Register the subscription in a hub connection registry as soon as permission attachment succeeds. One helper atomically claims cleanup, invokes `ConversationPermissionBroker.detach_browser` at most once, lets every later caller await the same task, and removes the connection registry entry only after completion.
   - Route attach exceptions, attach-time replay rejection, explicit/peer detach, replay-unavailable closure, live overflow, and WebSocket `finally` through that helper. Synchronous fanout claims and schedules the one task; finalization awaits that same task rather than invoking detach again. Server closure/logging remains idempotent separately, so the warning also occurs once.
   - Change the unit permission fake to count calls, then add `test_replay_rejection_and_finalization_detach_permission_once`, `test_live_overflow_and_finalization_detach_permission_once`, and `test_websocket_finalization_race_awaits_claimed_permission_detach_once`. Coordinate the fake with events so overflow/rejection and finalization genuinely overlap; assert one call, completed cleanup, and no orphaned detach task—not merely eventual absence from a set.

4. **Add exact, content-safe closure logs.**
   - Centralize replay-unavailable and live-overflow closure. Emit exactly one single-line JSON warning with event `conversation_browser_subscription_closed` and fields `closure_phase`, `employee_id`, `acp_session_id`, `binding_generation`, `connection_id`, `close_reason`, `replay_envelope_count`, `replay_byte_count`, `replay_byte_limit`, `live_queue_envelope_count`, and `live_queue_capacity`.
   - Populate it only from typed identity, counters/limits, and fixed close reasons. Never serialize replay items, prompts, transcript text, arbitrary actions, or exception bodies. The byte-failure and slow-live tests parse the record, assert exact fields/counts, assert one record, and prove a sentinel transcript string is absent.

5. **Move the tunable production limit to configuration and prove composition.**
   - Add `ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES: Final = 1_024` to `src/planner/conversation/configuration.py`. Import it into `hub.py` for the constructor default and into `composition.py` for `ConversationComposition.build(test_options=None)`; remove literal production `128` values.
   - Keep `ConversationTestOptions.browser_capacity` injectable. In `tests/unit/test_acp_conversation_composition.py`, add `test_production_composition_uses_1024_browser_capacity_and_test_options_can_override`: build once with `test_options=None` and assert the hub uses the configuration constant, then build with a small explicit test capacity and assert the override. No backend session needs to spawn.

6. **Preserve shutdown and document the resulting behavior.**
   - Add `test_shutdown_stops_pending_bootstrap_with_existing_close_reason` only to assert the existing shutdown reason/state, no further bootstrap send after closure is observed, and awaited writer cancellation. Do not require shutdown to remove browser registrations or detach permissions, and do not change composition shutdown ordering.
   - Update `docs/chat.md` to explain integrity-checked subscriber-local replay, repeated reset cutovers for connected browsers, the 1,024-envelope live queue, genuine slow-client eviction, and content-free closure logs. Update `PROGRESS.md` and `decisions.md` only with completed implementation evidence and actual judgments.

## Focused gates

```sh
.venv/bin/ruff check src/planner/conversation/hub.py src/planner/conversation/configuration.py src/planner/conversation/composition.py tests/unit/test_conversation_hub.py tests/unit/test_acp_conversation_composition.py tests/e2e/test_acp_conversation.py
.venv/bin/mypy src/planner/conversation/hub.py src/planner/conversation/configuration.py src/planner/conversation/composition.py
.venv/bin/pytest -q tests/unit/test_conversation_hub.py tests/unit/test_acp_conversation_composition.py
.venv/bin/pytest -q tests/e2e/test_acp_conversation.py -k 'real_websocket_replay_larger_than_live_queue_reaches_ready_then_delivers_live_update or existing_websocket_crosses_large_replacement_replay_without_partial_transcript or active_vertical_replay_fails_closed_without_harming_live_browser'
```

After independent review reports no unresolved contract violation and integration is settled, run one canonical repository gate:

```sh
./verify
```
