# ACP-01 implementation review — correction check

This is a narrow finding-by-finding check of the seven findings in
`implementation-review-round-1.md`. It does not reopen confirmed areas or inspect concurrent ACP-03
work.

## Finding 1 — RESOLVED

Raw reservation is now transactional and alias-only
(`src/planner/conversation/ordered_ingress.py:202-239`). The observer calls the pinned SDK model with
`strict=True`, `by_alias=True`, and `by_name=False`; it constructs and appends the complete valid or
rejected slot before advancing `_next_ordinal`. `_rejected_discriminator()` maps missing, untrimmed,
and control-character values to the frozen display-safe `"missing"` value
(`ordered_ingress.py:72-87`).

The new parameterized test covers snake-case fields, an unsafe future discriminator, missing update,
partial known update, and ordinary future update. Every case reserves and consumes exactly one
`ProtocolUpdateRejectedPayload` and completes the load barrier. Re-running the original unsafe and
snake-case probes produced:

```text
malformed 1 1 1 ProtocolUpdateRejectedPayload missing
malformed 2 1 1 ProtocolUpdateRejectedPayload missing
```

No observer exception or unreachable ordinal remains.

## Finding 2 — RESOLVED

Every retirement now publishes a terminal cause before consumer cancellation.
`retire_without_wait()` calls `_fail()`, closes acceptance, wakes the shared condition, and only then
cancels a non-draining consumer (`ordered_ingress.py:348-388`). `_fail()` settles an unfrozen load
target directly, while a response-observed waiter sees the same `_fatal_error` through
`wait_until_consumed()` (`ordered_ingress.py:257-266,328-342`).

The SDK child uses that path for force close and passes the actual lifetime cause from unexpected
process exit or other teardown (`src/planner/conversation/sdk_child.py:341-352,470-487`). Tests now
cover direct non-draining retirement, sink failure, force close, process death after the load
response, and shutdown while load/sink work is blocked. The original direct probe now returns:

```text
terminal_waiter RuntimeError terminal
```

The waiter no longer survives its consumer or child generation.

## Finding 3 — RESOLVED

Overflow stops further raw reservations but no longer clears accepted unfulfilled slots. Typed
callbacks may still fulfill those existing slots, and the single consumer continues draining them
in order (`ordered_ingress.py:202-211,241-255,294-326`). For an overflow fatal, the SDK lifetime
keeps the official connection open until every reserved slot is fulfilled and the complete accepted
prefix leaves the sink (`sdk_child.py:450-458`).

The new delayed-callback test yields a full event-loop turn after overflow before fulfilling either
accepted occurrence. Both are delivered exactly once and the overflowing third frame is not
reserved. The original adversarial probe now produces:

```text
overflow_prefix ['a', 'b']
```

This closes the accepted-prefix/no-drop gap from round one.

## Finding 4 — RESOLVED

`_replace_conversation()` now guards every step after successful `session/new`. Any exception before
replacement publication generation-retires the matching record and closes its child
(`src/planner/conversation/employee_registry.py:192-245`). The durable binding remains authoritative,
so the next demand allocates a fresh child generation and loads either the unchanged old binding or
the already-persisted CAS winner.

Focused tests separately prove CAS exception recovery to the old binding, CAS-loser teardown and
winner adoption through a fresh child, and post-CAS durable-reread failure followed by fresh loading
of the persisted candidate. All three passed.

## Finding 5 — RESOLVED

Registry shutdown now uses `asyncio.wait` against one absolute deadline rather than
`wait_for(gather(...))` (`employee_registry.py:406-479`). It gives graceful work a bounded first
phase, cancels pending work, launches available child force-close operations concurrently, waits
only for the remaining absolute budget, and does not gather cancellation-resistant tasks after the
deadline. Late task results are consumed through callbacks without delaying the raised typed error.

The cancellation-resistant factory test, two-child concurrent force-close test, and blocked-sink
test all assert return within a small tolerance of the supplied deadline and verify sorted unfinished
employee IDs. Partial factory, initialize, and load shutdown cases also pass. The round-one hard
deadline failure is no longer reproducible.

## Finding 6 — RESOLVED

After joining an employee-ID wave, `get_or_spawn`, `attach`, and `new_conversation` now compare the
returned record against the complete requested `ConversationEmployee`, live child, and backend
(`employee_registry.py:100-190,523-531`). An incompatible result is re-evaluated through a new wave,
which performs the explicit backend transition instead of returning the joined record.

The new gated test independently exercises incompatible concurrent get, attach, and new-conversation
waves. In every case the beta caller receives a beta record and a beta child is created where
required. The alpha wave is never returned as the beta result.

## Finding 7 — RESOLVED

The focused suite now contains the lifecycle evidence missing in round one: independent employee
progress, failed-load/no-remint recovery, CAS exception and loser teardown, post-CAS reread recovery,
late old-generation death, incompatible waves for all three public operations, partial
factory/initialize/load shutdown, cancellation-resistant absolute deadlines, concurrent all-child
force close, blocked-sink shutdown, protocol/name/version/load-capability initialize rejection,
delayed accepted-prefix overflow, process death during a response-observed load, and forced shutdown
during stderr flood.

The direct ACP-00 production-subject calls for probes 1, 6, and 9 remain in
`test_production_runtime_passes_acp01_conformance_probes_and_mutations_fail`; the early-load-ready,
binding-drift, and callback-reorder mutations still fail their corresponding unchanged assertions.
That test passed in both the full focused run and the narrow seven-test rerun.

## Focused evidence

```text
.venv/bin/pytest -q tests/unit/test_acp_employee_child.py \
  tests/unit/test_acp_employee_registry.py \
  tests/unit/test_hermes_acp_backend.py \
  tests/unit/test_acp_conformance_harness.py
......................................................................   [100%]
70 passed

Narrow CAS/shutdown/incompatible-wave/production-probe rerun:
7 passed
```

The direct malformed, terminal-waiter, and delayed-overflow probes also passed with the outputs
quoted above. No `./verify` run was performed.

## Verdict

**READY** — all seven round-one findings are resolved. No second broad implementation review is
needed.
