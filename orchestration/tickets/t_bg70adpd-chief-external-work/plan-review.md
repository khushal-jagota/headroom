# Codex plan review

Command: `codex exec --model gpt-5.5 --sandbox read-only -c model_reasoning_effort=high ... < /dev/null`

Codex found nine gaps in the accepted plan:

1. The current binary `human`/`agent` request seam cannot express actor-neutral ordinary commands plus explicit Chief commands without a worker bypass.
2. The external-work request body was not exact.
3. Required settled fields for each target state were not defined.
4. Pending-proposal behavior was not defined.
5. Active worker/chat control and non-empty statuses were not defined.
6. State, ceiling, at-cap, and ticket-status consistency was not defined.
7. Existing event sequence and readiness wake were not exact.
8. CLI options, terse/JSON tests, and skill obligations needed exact coverage.
9. No reconciliation table/object, ambiguity flow, evidence model, proposal system, or new event kind should be added.

All nine are accepted. `implementation-dispatch.md` makes each boundary concrete. The implementation must not begin until the adjusted dispatch receives a no-violations follow-up review.
