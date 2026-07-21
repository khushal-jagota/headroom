# ACP-01 implementation review — round 1

## Verdict

**NOT READY.** The official-SDK happy path and the existing focused suite pass, but the settled
runtime still has three ordered-ingress/load blockers, two registry lifecycle violations, and an
incompatible-demand coalescing error. Several of those cases are named acceptance requirements but
are absent from the focused tests, which is why the reported 44-test pass does not detect them.

## Findings

### 1. Blocker — malformed raw updates can bypass rejection or strand the load barrier forever

`OrderedAcpConversationIngress._reserve()` increments the reserved ordinal before it has
successfully constructed a slot, and its raw validation is not alias-only
(`src/planner/conversation/ordered_ingress.py:197-217`). This creates two concrete failures:

- `SessionNotification.model_validate(params, strict=True)` inherits the SDK model's
  `populate_by_name=True`, so raw JSON using Python names such as `session_id` and
  `session_update` is accepted and normalized into a valid typed notification. The corrected plan
  requires strict ACP aliases at this observer seam; malformed wire input must become
  `ProtocolUpdateRejectedPayload`, not be silently repaired and forwarded.
- `_rejected_discriminator()` returns an arbitrary nonblank string. A JSON-valid discriminator such
  as `"future\nupdate"` then fails the frozen payload's display-safe validator. The ordinal has
  already advanced, but no slot is appended and no fatal error is recorded. The pinned SDK 0.11.0
  catches and logs synchronous observer exceptions before continuing dispatch, so the matching load
  response freezes an unreachable target ordinal and `finish_load_epoch()` waits forever.

A deterministic direct probe produced:

```text
observer_exception ValidationError
load_finished False
reserved 1 consumed 0 received 0
```

Make reservation transactional: validate with `by_alias=True, by_name=False`, sanitize every raw
discriminator into the frozen display-safe domain, and append exactly one valid or rejected slot
before advancing the externally visible ordinal. Add load tests for snake-case fields, unsafe
unknown discriminators, missing/partial updates, and future updates. Every case must finish promptly
with one visible rejection and no observer exception.

### 2. Blocker — fatal or forced ingress retirement does not wake a response-observed load

`wait_until_consumed()` exits only on consumption or `_fatal_error`
(`ordered_ingress.py:236-245`). However, `close(drain=False)` merely marks the ingress closed,
cancels the consumer, and returns without calling `_fail()` (`ordered_ingress.py:302-323`). The SDK
child uses exactly this non-draining close on unexpected process exit, ingress-fatal teardown, and
task cancellation (`src/planner/conversation/sdk_child.py:446-459`).

If the load response has frozen a target while its sink is blocked, process death or forced close
cancels the only consumer but leaves `finish_load_epoch()` waiting for an ordinal that can never be
consumed. A deterministic response-observed/sink-blocked probe followed by `close(drain=False)`
produced `close_waiter_done False`.

All terminal ingress paths need one terminal cause that fails both the load-target future and every
consumption waiter before the consumer is cancelled. Prove unexpected death, overflow, sink failure,
forced close, and shutdown cannot leave `load_session()` pending after the child is dead.

### 3. Blocker — overflow drops raw-accepted updates whose typed callbacks have not run yet

Overflow records `_fatal_error` immediately. The consumer drains only the already-fulfilled head,
then clears every remaining slot as soon as it sees the fatal state
(`ordered_ingress.py:247-276`). The child simultaneously begins fatal teardown. Raw reservations
accepted before the overflow therefore disappear if their SDK callback tasks are delayed by one
event-loop turn, even though those frames were within the configured bound.

A direct probe reserved `a` and `b` at a limit of two, overflowed on `c`, yielded once before the
typed callbacks, then fulfilled `a` and `b`. It produced:

```text
overflow_received []
overflow_fatal AcpSessionUpdateIngressOverflow
```

The current test only fulfills its one accepted item before the consumer observes the fatal state,
so it does not prove the contract's accepted-prefix/no-drop rule. After overflow, raw acceptance must
stop, but already reserved occurrences must remain fulfillable and drain in wire order while the
sink can progress; the overflowing occurrence is the rejected one. Add a latch test that delays all
accepted typed callbacks until after overflow is recorded and asserts the complete accepted prefix
is delivered exactly once before retirement.

### 4. High — `new_conversation` persistence failure leaves the candidate child published against the old binding

`_replace_conversation()` creates the new ACP session and then awaits CAS without any teardown guard
(`src/planner/conversation/employee_registry.py:182-198`). If CAS raises, the current child remains
alive and the old `AcpEmployeeRecord` remains the fast-path record even though that process has
already created the unpersisted candidate session. This violates the explicit rule that persistence
failure closes the candidate child and publishes nothing.

A deterministic failing-CAS probe showed:

```text
child_alive_after_new_cas_failure True
same_old_record_returned True
new_session_calls 2
```

Retire/close the matching child generation on every failure after `session/new` and before binding
publication. The durable old binding must remain unchanged; the next demand must use a fresh child
generation and load that durable winner. Cover CAS exception, CAS-loser adoption, and failure during
the post-CAS durable re-read/publication check.

### 5. High — the supplied shutdown deadline is not a hard bound

The registry uses `asyncio.wait_for(gather(...), timeout=remaining)` and, after timeout, awaits each
`force_close()` serially and then awaits the cancelled task set with no bound
(`employee_registry.py:380-415`). `wait_for` waits for cancellation to finish before raising, so a
close/factory/user callback that suppresses cancellation can prevent the timeout branch from being
reached at all. The later force-close and gather awaits can also exceed the already-expired deadline.

With a live protocol-conforming fake child whose `close()` delays cancellation until a latch, a
shutdown given a 20 ms absolute deadline was still pending after 100 ms:

```text
shutdown_returned_by_deadline False
eventual_result shutdown_error
```

Use deadline-aware waiting that does not await cancellation-resistant injected work after the
absolute deadline. Force the owned SDK process/lifetime contexts concurrently within the remaining
budget, record unfinished employee IDs, and raise `AcpEmployeeRegistryShutdownError` promptly. The
named partial-factory, initialize, load, blocked-sink, stderr-flood, and multiple-child shared-budget
shutdown cases need deterministic tests.

### 6. High — coalescing by employee ID can return a record for the wrong requested backend

Initialization, attach, and new-conversation tasks are keyed only by `employee_id`
(`employee_registry.py:99-180`). Unlike the live-record fast path, joining an existing task does not
verify that the task's `ConversationEmployee` or backend matches the current request. Two concurrent
demands for the same employee ID but different backend selections therefore coalesce incompatibly.
A gated probe returned the alpha record to the beta caller and spawned no beta child:

```text
alpha_result_backend alpha
beta_result_backend alpha
same_record True
beta_children 0
```

Coalesce only equivalent employee/backend demands, or re-evaluate the requested backend after the
joined wave settles and perform the required explicit backend transition. Apply the same rule to
attach and new-conversation waves; no public call may return a record that fails the method's own
employee/backend fast-path predicate.

### 7. High — named lifecycle acceptance evidence is materially incomplete

The focused tests contain eight registry cases and sixteen child/ingress cases, but do not implement
several tests promised by the contract and reviewed plan. Missing cases include independent employee
initialization, failed-load/no-remint, CAS-loser adoption, persistence-failure teardown, late death
against a replacement, partial factory/initialize/load shutdown, shared-deadline all-child shutdown,
protocol/version/load-capability initialize mismatch, delayed accepted-prefix overflow, and process
death during a response-observed load barrier. The deterministic failures above all pass the current
44-test command, demonstrating that this is a material acceptance gap rather than optional coverage.

Add the named cases without weakening the ACP-00 assertions. In particular, retain the direct
production-subject probe 1/6/9 calls and their three mutation checks; those existing checks pass and
should remain the shared assertion vocabulary.

## Checks and confirmed conforming areas

I inspected every ACP-01 file named in `implementation-report.md`, the ACP-00/00a/00b contracts, the
corrected plan and reviews, and the relevant installed `agent-client-protocol==0.11.0` source. I also
read the pinned Hermes ACP initialize implementation without changing or running git in the Hermes
checkout. It confirms `agentInfo.name="hermes-agent"`, version `0.18.2`, ACP protocol 1, and
`loadSession=True`.

The following focused checks passed on the reviewed tree:

```text
.venv/bin/pytest -q tests/unit/test_acp_employee_child.py \
  tests/unit/test_acp_employee_registry.py \
  tests/unit/test_hermes_acp_backend.py \
  tests/unit/test_acp_conformance_harness.py
44 passed

.venv/bin/pytest -vv \
  tests/unit/test_acp_employee_registry.py::test_production_runtime_passes_acp01_conformance_probes_and_mutations_fail \
  tests/unit/test_acp_employee_registry.py::test_old_generation_sink_must_finish_before_replacement_publication \
  tests/unit/test_acp_employee_child.py::test_sink_exception_is_generation_fatal_and_wakes_load_barrier \
  tests/unit/test_acp_employee_child.py::test_malformed_load_replay_becomes_typed_rejection_and_barrier_completes
4 passed

.venv/bin/ruff check <ACP-01 production and changed support/test files>
All checks passed!

.venv/bin/mypy src/planner/conversation
Success: no issues found in 9 source files
```

The reviewed code correctly uses the public SDK spawn/connection path in production, keeps the test
malformed-frame escape hatch out of production, maps backend-owned cwd and ordered additional roots,
sends an explicit empty MCP list, fails closed over the SDK default-environment floor, applies exact
Panels ticket/chief identity, matches the pinned Hermes definition, serializes downstream sink calls,
wakes the load barrier on sink failure, quiesces an admitted generation-N sink before N+1
publication, avoids the global registry lock across injected async calls in the reviewed paths,
drains bounded stderr, and settles the exercised expected/unexpected child-death paths once.

The concurrent ACP-03 donor hash and managed-markdown caller-inventory mismatches were explicitly
excluded from this review and are not ACP-01 findings. No `./verify` run was performed.
