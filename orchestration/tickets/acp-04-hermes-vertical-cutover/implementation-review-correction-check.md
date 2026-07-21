# ACP-04 implementation review — bounded correction check

## First correction-check output

The live Vite → uvicorn → official SDK boundary and the three production corrections passed
inspection: stale-session filtering, replay-capacity reservation, and startup-failure cleanup.

One P1 remained: Phase 8 cases 3, 6, and 7 still lacked the required vertical e2e proof.

- The two-browser replacement did not inject a stale old-binding source update.
- The worker e2e proved callback ordering and typed text, but not exact gateway interrupt or
  rejection/capture failure before queued-successor start.
- Slow-consumer/reset-overflow existed only in unit tests, not the Phase-8 e2e suite.

## Final disposition

`READY`

The retained proof finding is closed at the production-composition and official-child boundary in
`tests/e2e/test_acp_conversation.py`:

- the two-browser replacement injects stale old-session ingress and proves generation 2 remains live;
- the real automatic runner is interrupted through the exact synchronous gateway;
- protocol rejection errors the tracked worker and rejects the queued successor before another child
  prompt, while capture failure removes the worker collector before the queued successor starts;
- a live WebSocket remains healthy while an independent slow subscriber is evicted, and an active
  replay with a one-byte buffer limit fails unavailable with an empty queue and no partial prefix.

The orchestrator independently reran the complete vertical file after the final correction: `9 passed`.
No second broad review was opened.
