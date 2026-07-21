# ACP-01 plan review — correction check

This is a narrow check of the five findings in `plan-review-round-1.md`. It does not reopen the
broader plan review.

## Finding 1 — RESOLVED

The corrected contract now defines one ordered `AcpConversationIngress` for exact
`SessionNotification | ProtocolUpdateRejectedPayload`, reserves a bounded slot for every raw
`session/update`, permits exact SDK-model validation only for matching/rejection, and keeps the typed
SDK callback as the sole source of valid payloads (`contract.md:42-50,97-101,106-115`). The plan gives
the mechanism an executable algorithm: the observer validates in strict alias mode, records only a
canonical fingerprint for valid frames, immediately fills invalid slots with the frozen rejection,
and the typed callback fulfills the earliest matching reservation (`plan.md:88-106`). The load epoch
freezes the pre-response target ordinal and waits for every valid or rejected slot to pass through the
ordered sink; child death, overflow, unmatched fulfillment, or shutdown wakes the waiter with failure
(`plan.md:164-183`). The malformed/future replay fixture and acceptance cases explicitly prove prompt
completion without a typed callback, quiet-period heuristic, or unbounded wait
(`plan.md:67-75,360-377`). This implements decision
`D-acp-observer-reserves-typed-ingress` without making the observer a second valid-payload path.

## Finding 2 — RESOLVED

The corrected contract requires a per-employee publication/update gate and states that generation
N+1 cannot become visible until every admitted N sink call has returned (`contract.md:121-129`). The
plan specifies a consistent employee-gate-then-global-lock order for publication and ingress. An
ingress call releases the global lock before awaiting user code but retains the employee gate;
publication therefore waits without holding the global registry lock across the sink
(`plan.md:254-282`). The acceptance map includes the exact missing latch case: pause N inside the
sink, prove N+1 publication waits, then prove no N callback enters after publication
(`plan.md:360-380`). This satisfies
`D-acp-generation-publication-quiesces-old-sinks`.

## Finding 3 — RESOLVED

The corrected plan explicitly repairs `tests/support/acp_reference_subject.py`: every direct
`session/load` uses the production observer-slot/typed-consumer barrier before the subject records
load return, while `assert_load_replay` remains unchanged. A consumer latch proves the raw response
may arrive while that wrapped reference load remains pending (`plan.md:67-71`). The same requirement
is present in the race-case ledger and implementation order (`plan.md:369-389`). Adding the
production ACP-01 subject no longer leaves the reproduced ACP-00 reference-subject race untouched.

## Finding 4 — RESOLVED

The generic path now calls the selected backend definition's `working_directory_for(employee)` for
every process spawn, new session, and load. Additional directories preserve declared workspace-root
order, exclude the selected cwd when it is one of those roots, and retain every root when the backend
selects a different absolute cwd (`plan.md:221-240`). A fake backend selecting the second root must
prove process/new/load mapping, while the Hermes-specific definition continues to prove its required
first-root policy (`plan.md:320-365,369-377`). Generic runtime no longer hard-codes Hermes's cwd.

## Finding 5 — RESOLVED

The corrected plan accounts for the pinned SDK's implicit `default_environment()` merge before
spawn. The factory computes the actual SDK-default mapping and fails closed if any present default
name is absent from the backend definition's declared inherited-name set
(`plan.md:336-352`). The acceptance map and race-case ledger require a non-Hermes fake definition
that omits a present SDK default and prove fail-before-spawn behavior
(`plan.md:360-377`). Hermes deliberately declares the six POSIX defaults, so its concrete launch and
the generic allowlist semantics are both explicit.

## Verdict

**READY** — all five round-one findings are resolved in the corrected contract and implementation
plan. No second broad review is needed.
