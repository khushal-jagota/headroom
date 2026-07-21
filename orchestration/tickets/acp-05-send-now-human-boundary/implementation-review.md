# ACP-05 Send Now post-recovery human-boundary implementation review

## Verdict

**READY**

No concrete contract violation was found.

## Reviewed evidence

- `ConversationRequestedCancelRecoveryTransitionPort` carries one optional, typed
  `send_now_successor_human_echo`; no public wire shape or browser heuristic was added.
- `ConversationHub.commit_requested_cancel_recovery_transition` validates every retained FIFO prompt
  and the optional successor prompt against the unchanged durable ACP session before replacing or
  publishing the stream. Its single employee-sequencer callback publishes reset, replay, ready, FIFO
  human echoes, the optional successor human echo, and the FIFO-only queue snapshot in that exact
  order, then settles the transition.
- The broker rebuilds the captured successor on the complete replacement handle, constructs the echo
  from that successor's exact client message ID and `PromptRequest`, waits for the hub commit, settles
  the predecessor once, and only then starts the successor. Stop passes no successor echo; retained
  FIFO intent still reaches the hub and advances once on the replacement.
- The hub unit regression asserts the exact six-envelope order for two attached browsers, exact FIFO
  and successor prompt payloads, and exclusion of the successor from the queue snapshot. The broker
  regressions assert a blocked commit prevents successor start, one exact successor echo reaches the
  port, Stop carries `None`, and FIFO/successor starts remain single.
- The official-SDK e2e asserts post-reset FIFO and successor echoes exactly once, their order before
  the FIFO-only snapshot and successor `started` receipt, one successor answer, one successor/FIFO
  start, unchanged binding, fresh child identity, and absence of late old-generation output.
- The browser regression consumes the production-shaped reset/replay/ready/FIFO-echo/successor-echo/
  snapshot/start/output sequence and requires separate old-agent, user, and successor-agent messages;
  removing the post-reset successor boundary would reproduce the production concatenation and fail
  the assertion.

Per the review request, I did not run tests. The focused-check output in the ticket was inspected but
not independently re-executed.
