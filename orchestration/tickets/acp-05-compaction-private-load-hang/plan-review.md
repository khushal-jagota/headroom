# ACP-05 asynchronous compaction handoff plan review

## Verdict

**NOT READY.** One implementation-blocking correction is required.

## Blocker

1. **The plan assumes a pre-publication quarantine that the hub does not provide, so legal candidate
   updates are still dropped and failure paths cannot prove buffer settlement.** Phase 4 says the hub's
   existing pre-binding capture will quarantine candidate notifications, and Phase 6 only pops the exact
   replacement source-key buffer during commit. The current `_ingest_source` does the opposite while the
   generation-N stream is ready: a non-current source is returned from without buffering, while a
   candidate-session update emitted by the source child uses N's current source key and is passed to
   `_publish_source_payload`, which drops it on the session-ID mismatch. The real Hermes ordering in this
   ticket exercises exactly the latter case: `session/fork` schedules a candidate-session update on the
   source child after the fork response. A fresh candidate child can also emit an ordinary post-load
   update before registry publication; that update currently takes the non-current-source drop. Merely
   popping the replacement source key at commit therefore cannot satisfy frozen behaviors 3 and 9.

   The plan also has no ownership link by which abort, generation-fatal failure, external-winner
   adoption, transition expiry, or shutdown can identify and clear all buffers created by this
   compaction. The assertion proposed at the end of Phase 6 does not define a mechanism and is
   insufficient for frozen behavior 14. An unused fork candidate can leave a different source-key buffer
   behind when an external winner is adopted.

   **Exact correction:** revise Phases 1, 4, and 6 so the active `_CompactionTransitionState` owns one
   aggregate-capacity-bounded FIFO quarantine whose entries retain both the exact child source key and
   the validated session ID. During an active compaction transition:

   - current-source updates for N continue publishing normally before reset;
   - a candidate-session update from the original source child, and any update from an unpublished
     source, enter the transition-owned quarantine instead of the ordinary stream or the global
     ready-stream drop path;
   - commit, already serialized by the employee sequencer, publishes `reset`, the private replay, then
     exactly the quarantined entries belonging to the chosen N+1 session and permitted source child or
     children in their admitted FIFO order, once each, before `ready`, human echoes, and the queue
     snapshot; later N+1 ingress follows `ready` through the ordinary path; and
   - normal commit discards non-winning-source entries, while abort, fatal failure, external-winner
     cleanup, expiry, and shutdown synchronously clear the whole transition-owned quarantine and settle
     its capacity/waiter state.

   Add hub/registry integration cases for both legal origins: the post-fork candidate update arriving on
   the original source child and a post-load update arriving on the unpublished fresh child. Pin two-
   browser order and exactly-once delivery, prove an ordinary N update remains before reset, prove an
   admitted post-commit N+1 update remains after ready, and assert quarantine emptiness after normal win,
   external winner, pre-CAS failure, expiry, cancellation, and shutdown. Do not rely on `_source_capture`
   becoming populated under its current ready-stream rules.

## Other reviewed invariants

No additional blocker was found in the session-aware ACP routing, lifecycle/publication lock order,
fresh-generation reservation and death cleanup, pre-CAS source invalidation, durable CAS ambiguity and
external-winner handling, broker rekey/FIFO settlement, or the single 300-second absolute budget and
immediate phase/cause diagnostics.
