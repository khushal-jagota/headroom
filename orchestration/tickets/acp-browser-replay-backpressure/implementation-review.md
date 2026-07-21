# ACP browser replay and live backpressure implementation review

## First review

**Not ready.** The independent standards and contract reviews found four load-bearing defects:

1. Compaction and requested-cancel recovery still published replacement replay through each
   browser's bounded live queue. Their tests called `_bind_stream` directly and therefore did not
   exercise the broken production transitions.
2. A first-load or idle-refresh subscriber received its cutover marker in the bounded live queue
   before its WebSocket writer started, so a capacity-one queue plus one interleaved update could
   still produce a false slow-client eviction.
3. Replay-classification failures escaped as a generic transport failure instead of closing before
   a partial transcript with the replay-unavailable reason and structured warning.
4. The warning's `live_queue_envelope_count` included replay bootstrap envelopes rather than only
   bounded live entries.

## Resolution

- Compaction, requested-cancel recovery, and ordinary refresh now use one detached candidate build,
  integrity check, and subscriber cutover path. Focused tests call both public transition APIs with
  replay larger than live capacity.
- The subscriber that triggered first load or refresh receives an initial subscriber-local
  bootstrap. Existing subscribers receive an ordered cutover outside live capacity.
- Replay construction failures commit an unavailable replay epoch, expose no prefix, close affected
  subscriptions with the existing replay-unavailable reason, and emit the content-free warning.
- Permission detachment is one subscription-owned task and race tests prove one broker detach call.
- Logs use the bounded queue's dedicated live count.
- A real `/api/conversation` WebSocket test keeps an existing socket connected across a large idle
  refresh and proves complete reset/replay/ready delivery followed by the next live update.

## Second review

The standards reviewer reported **RESOLVED** for every source and observability finding. The contract
reviewer reported the source findings resolved and requested the real-WebSocket reset-transition
acceptance proof; that test was added and passed. No unresolved review finding remains.

## Focused evidence

- Ruff: all checks passed.
- Strict Mypy: no issues in the three changed source files.
- Hub and composition tests: 29 passed.
- Selected replay, slow-client, and real-WebSocket transition tests: 4 passed.
