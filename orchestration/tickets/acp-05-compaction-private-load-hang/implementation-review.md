# ACP-05 implementation review

## Verdict

**NOT READY.** Exact-session ingress, the fresh unpublished candidate topology, durable-winner adoption,
and the hub's bounded transition quarantine match the frozen design. Two lifecycle failures remain.

## Findings

### [P1] Retire N when compaction preparation fails before `session/fork`

`prepare_compaction_capture` uses `source_fork_started` as the condition for invalidating the source
generation. The flag remains false while the advertised capability is checked, so a missing
`session/fork` capability at `src/planner/conversation/employee_registry.py:1093` reaches the
non-generation-fatal branch at `src/planner/conversation/employee_registry.py:1229` and leaves the exact
source child registered and reusable. That child has already processed the compacting prompt. Frozen
behavior 11 says the source is no longer authoritative after `/compact` may have changed its process,
even when durable binding N is unchanged; recovery must retire N and freshly load authoritative N. The
parameterized regression at `tests/unit/test_acp_employee_registry.py:1412` currently permits the stale
path instead of asserting source invalidation.

Make every preparation failure after the compacting prompt fail the exact source generation, including
capability rejection before the fork RPC. Close the source, preserve durable N, report a generation-fatal
prepare failure, and prove a subsequent attach uses a fresh child to load N. Pre-admission failures on an
already stale/non-owned handle can remain non-fatal.

### [P1] Put publication-gate waits and settlement cleanup under the absolute deadline

The transaction's external calls use `_await_before_deadline`, but its publication-gate acquisitions do
not. Preparation (`employee_registry.py:1081`, `:1158`), candidate publication (`:1321`), external-winner
publication (`:1598`), and abort/final cleanup through `_discard_reserved_generation` and
`_invalidate_reserved_handle` (`:1523`, `:1653`) can wait indefinitely. `abort_compaction_capture` even
accepts the original deadline but never uses it (`:1462`). If an admitted public sink holds the gate, the
capture task can therefore outlive the 300-second breaker; its `finally` cannot release the lifecycle
reservation until the same unbounded cleanup completes. That violates frozen behaviors 13-14 and can
strand later Ticket or Chief work—the class of hang this correction is intended to eliminate. The
after-deadline abort regression at `tests/unit/test_acp_employee_registry.py:1465` only exercises a free
publication gate.

Use the original deadline for every compaction-path publication-gate acquisition. On expiry or
cancellation, run a fail-closed cleanup path that can invalidate/detach the exact source and candidate,
clear callback reservations, remove the prepared capture, and release the lifecycle gate without waiting
again on the blocked publication gate. Add held-gate regressions for preparation, final publication,
external-winner adoption, and abort, asserting bounded completion and complete settlement.

## Review scope

One read-only implementation review against the frozen contract, implementation plan and resolution,
both implementation reports, and the current ingress/registry/hub/broker tests and source. Per dispatch,
no product or test files were edited and neither Codex CLI nor `./verify` was run.
