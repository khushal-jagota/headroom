# ACP-01 plan review — round 1

## Findings

### 1. Blocker — the count-only load barrier has no completion or failure path for an invalid replay notification

The proposed barrier counts every raw incoming `session/update` before the matching load response,
then waits for the same count to complete through the typed sink (`plan.md:141-159`). That works for
valid notifications, but it cannot complete for a partial or future ACP update. In the pinned SDK,
the stream observer runs before dispatch (`.venv/lib/python3.14/site-packages/acp/connection.py:153-168`),
so it increments the raw target. The client router then validates `SessionNotification` before
calling the Panels client (`.venv/lib/python3.14/site-packages/acp/router.py:93-107` and
`.venv/lib/python3.14/site-packages/acp/client/router.py:165`). If validation fails, the typed
`session_update` callback is never invoked; the SDK's notification runner suppresses the exception
(`.venv/lib/python3.14/site-packages/acp/connection.py:268-271`). There is consequently no typed
enqueue, consumed-count advance, or public SDK failure signal to wake step 4 of the plan. A load with
one malformed replay frame followed by a successful response waits forever rather than failing
visibly.

This is not an optional robustness case. The reviewed program requires a partial/unknown replay to
produce `protocol_update_rejected` (`orchestration/acp-migration/plan.md:127-135`), while the ticket
allows the observer only as an order/count barrier and forbids it from decoding payloads
(`contract.md:96-104`). The current SDK offers no public typed validation-error callback that makes
both statements true as written. `plan.md:11-16` therefore incorrectly concludes that there is no
contract contradiction.

**Required correction:** return this seam to the orchestrator for an explicit contract decision
before implementation. The corrected design must deterministically account for both valid and
invalid pre-response notifications and wake the load waiter with either typed-consumer completion or
a typed protocol rejection/failure. It may explicitly authorize strict SDK-model validation at the
observer boundary, or choose another documented SDK-supported dispatch/error seam, but it must not
add a quiet-period heuristic or leave an unbounded count wait. Add a scripted load containing a
partial/future update and prove that the public load/attach call fails visibly and promptly rather
than hanging or flattening it.

### 2. Blocker — checking child generation only before an awaited sink does not guard N+1 from an in-flight N update

The plan says the generation wrapper checks that N is current *before awaiting* the domain sink and
claims this prevents N from mutating N+1 (`plan.md:245-248`). The sink itself is the mutating or
broadcasting operation. This interleaving remains possible:

1. generation N passes the check and enters a deliberately slow sink;
2. the registry publishes generation N+1 while that await is suspended;
3. generation N's sink resumes and commits/broadcasts after N+1 is current.

The existing proposed test checks callbacks arriving after N+1 publication
(`plan.md:332-339`), so it does not exercise this race. It violates the frozen requirement that a
late update from N cannot mutate N+1 (`contract.md:74-78`) and makes the plan's own stale-generation
claim false.

**Required correction:** serialize per-employee update admission/execution with generation
publication without holding the global registry lock across user code. For example, a per-employee
publication/update gate can make a sink invocation re-check and run while publication is excluded,
and make publication advance the generation only after every admitted old-generation invocation has
left the gate. Fully quiescing the old ingress before publication is also valid if it is bounded and
preserves the ticket's no-drop rule. Add the missing latch test: pause N inside the sink, attempt to
publish N+1, and prove either that publication waits for N to finish or that N is settled before N+1
becomes visible; after publication, no N callback may enter the sink.

### 3. High — the plan leaves the reproduced ACP-00 reference-subject load race in the required focused suite

The plan adds a production runtime subject for probes 1, 6, and 9 (`plan.md:52-57`), but does not
correct the existing direct-SDK reference subject. That subject records `load_response` immediately
after `ClientSideConnection.load_session` returns and only then waits for callback consumption
(`tests/support/acp_reference_subject.py:261-277`). The broader run has now reproduced the exact race:
the SDK load return was recorded before the typed callback enqueue. This follows directly from the
SDK: the receive loop resolves a response directly (`acp/connection.py:193-203,273-290`), while prior
notifications are published to a dispatcher that starts separate callback tasks
(`acp/task/dispatcher.py:56-65,90-94`).

ACP-01's focused commands still require the full existing ACP-00 conformance suite
(`plan.md:357-367`), and the ticket says ACP-00 behavior must remain green without weakening its
assertions (`contract.md:184-189`). Adding a second, correct production subject does not make that
existing subject deterministic.

**Required correction:** extend the plan to give the ACP-00 reference subject the same deterministic
observer-target → typed-consumer-completion barrier for every direct `session/load` it performs (or
route those loads through the production child after doing so preserves the reference subject's
purpose). Keep `assert_load_replay` unchanged. Add a latch test in which the raw response is observed
while the typed consumer is blocked, and prove the reference subject cannot record the public load
return until consumption is released.

### 4. High — generic session requests bypass the backend definition's working-directory policy

`AgentBackendDefinition` freezes a `working_directory_resolver`, and its public
`working_directory_for` method is the backend-owned cwd policy
(`src/planner/conversation/backend_contracts.py:67-105`). The plan instead hard-codes the first
workspace root into generic new/load request construction (`plan.md:197-208`). "First root" is the
Hermes definition required by this ticket (`contract.md:57-63`); it is not a generic registry rule.
As written, a later conformant backend definition can select another declared root for its process
but still receive a different `session/new`/`session/load` cwd from generic runtime code.

**Required correction:** have generic child/registry construction call the selected definition's
`working_directory_for(employee)` for subprocess and ACP session cwd. Freeze one deterministic
additional-directory rule relative to that selected cwd (preserve declared root order and exclude
the selected root when it is one of them). Add a fake-backend test whose resolver selects a
non-first root, alongside the Hermes test that proves Hermes resolves to the first root. No generic
module should encode the Hermes-specific first-root choice.

### 5. High — the stated generic environment allowlist is not what `spawn_agent_process` actually launches

The plan says the generic factory builds a fresh mapping containing only definition-allowlisted
ambient names plus explicit overrides (`plan.md:303-315`). The pinned SDK unconditionally starts
from `default_environment()` and then overlays the supplied mapping
(`.venv/lib/python3.14/site-packages/acp/transports.py:13-44,47-74`). On POSIX that silently adds
`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER` even when a backend definition did not name
them. Hermes happens to declare those exact six, so its concrete definition is honest, but the
generic factory does not enforce the frozen `AgentBackendDefinition` inherited-name allowlist
(`src/planner/conversation/backend_contracts.py:70-99`) for any other definition.

**Required correction:** account explicitly for the SDK's public default-environment floor. With
the mandated `spawn_agent_process` API, the bounded solution is to fail before spawn when an
applicable SDK-default ambient name is present but absent from the backend definition's declared
inheritance set; every registered backend can then deliberately declare the defaults it accepts.
Add a polluted-environment test using a non-Hermes fake definition that omits one present SDK
default and prove the factory rejects it rather than launching with an undeclared variable. Preserve
the existing negative tests for `PLAN_*`, provider credentials, ambient `HERMES_HOME`, and
`HERMES_TUI_SKILLS`.

## Verdict

**NOT READY** — finding 1 requires an explicit orchestrator contract decision because the pinned SDK
does not expose the validation-failure signal assumed by the frozen observer restriction. Finding 2
is a publication race in a load-bearing invariant. Findings 3–5 are bounded plan corrections needed
to make the named focused proof, backend policy seam, and environment confinement executable.
