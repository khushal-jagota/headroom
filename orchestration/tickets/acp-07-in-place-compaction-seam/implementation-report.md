# ACP-07 generic in-place compaction seam — implementation report

Status: **focused gates passed; ready for integration review**

## Implemented

- Added the optional backend-strategy hook
  `capture_compaction_in_place(session_binding)` to the exact-generation strategy wrapper. The
  wrapper detects it only after reacquiring the exact runtime lease.
- The wrapper owns the existing actor-supplied absolute deadline. It cancels the provider coroutine
  normally on expiry or caller cancellation so provider `finally` cleanup can run.
- In-place completion accepts only terminal `ContextCompaction` values (`compacted` or `failed`).
  Invalid or untyped output is a concrete, non-generation-fatal `backend observation` failure.
- Only Panels' own timer winning becomes `ConversationCompactionCaptureDeadlineExpired` during
  `backend observation`, with the durable binding truthfully `stayed` on the original generation.
  A provider-raised `TimeoutError` remains an immediate concrete provider failure.
- The in-place branch never calls the runtime's prepare/fork/private-load, durable CAS/commit, abort,
  or replacement-transition path and never produces a capture transition.
- The pre-existing Hermes normalize/prepare/commit branch and its deadline helper are unchanged.

## Deterministic proof

`tests/unit/test_in_place_compaction_strategy.py` covers exact compacted and failed terminals,
invalid terminal and untyped output, concrete `RuntimeError` and immediate backend `TimeoutError`,
Panels timer expiry, provider cleanup on both expiry and caller cancellation, no fork/CAS/transition,
and lease failure before hook invocation.

All 27 existing Hermes turn-strategy tests also pass alongside the 9 new seam cases.

## Concurrent selector integration debt

Three older broker integration nodes currently fail before reaching compaction because they still
construct `AcpEmployeeRegistry(backend_definitions=..., child_factories=...)` while the concurrently
integrated selector changed the constructor to the paired runtime-definition authority. This is
pre-existing selector caller-closure debt, not a seam regression, and was left untouched because the
assigned source/test boundary excludes registry integration callers:

- `test_broker_capture_adopts_exact_replacement_handle_before_fifo_advances`
- `test_durable_cas_failure_invalidates_source_actor_and_queued_intent`
- `test_fatal_abort_after_normalization_failure_fails_actor_and_queued_intent`

Canonical `./verify` was not run; ACP-10 owns that final gate.
