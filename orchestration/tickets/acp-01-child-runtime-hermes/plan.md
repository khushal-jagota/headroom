# ACP-01 implementation plan

## Boundary and contract check

This slice adds an uncomposed production ACP child runtime, an async employee registry with durable
session binding, and the pinned Hermes backend definition. It does not add HTTP/websocket
composition, a turn broker, filesystem or terminal reverse services, product database writers,
browser code, or a legacy cutover. The Hermes checkout remains read-only; implementation and tests
must not run git commands or write files there.

The first independent review found one SDK/contract contradiction and four further load-bearing
gaps. The orchestrator corrected the contract: the stream observer may run the exact SDK model
validation only to reserve/match valid callback slots and materialize the frozen rejection for an
invalid notification. It remains forbidden to forward valid payloads or interpret ACP content. The
plan below also adds per-employee publication/update quiescence, repairs the direct reference
subject, uses backend-owned cwd, and fails closed over the SDK's default-environment floor.

The frozen `AcpEmployeeChild` request-model methods can wrap
the SDK's current keyword-style public methods by unpacking the exact generated models. The SDK
stream observer is sufficient for a load count/order barrier because it observes each incoming
frame synchronously before dispatch and observes the matching response before the request future is
resolved. It will not be used to deserialize session updates. The SDK's typed client callback
remains the only payload source.

The implementation stays within the ticket's allowed paths. Add these production modules:

```text
src/planner/conversation/
  ordered_ingress.py       # bounded observed slots, typed fulfillment, one serial consumer
  sdk_child.py             # official-SDK child, load barrier, process/stderr lifecycle
  employee_registry.py     # employee ownership, binding CAS, attach/new/shutdown
  hermes_backend.py        # the only Hermes-specific definition builder
```

Update `conversation/configuration.py` with the two runtime bounds below. Do not change any frozen
ACP-00 model, Protocol method, browser wire shape, or dependency pin. Runtime classes should be
imported from their purpose-named modules; there is no need to turn `conversation/__init__.py` into
a composition registry in this ticket.

## 1. Freeze the test evidence before runtime code

Start by extending the ACP-00 scripted machinery without changing the existing ten-probe behavior:

- Add `tests/fixtures/acp/hermes-shaped-replay-v1.json`. Build every entry as an exact SDK
  `SessionNotification`, serialized with aliases. In order it contains user content, separate
  thought, separate assistant content, a missing-message-ID thought/message sequence, tool start and
  progress for one tool-call ID, two plan snapshots, `available_commands_update`, `usage_update`,
  and `session_info_update` with `_meta.hermes.sessionProvenance`. The thought text occurs nowhere in
  an assistant content block.
- Extend `tests/support/acp_scripted_agent.py` with deterministic, opt-in script/launch switches for
  agent identity, Hermes-shaped history, stderr flooding, a numbered high-volume update burst, and
  process death. Keep default ACP-00 scripts byte-for-behavior compatible. Initialize/new/load may
  echo only fixed safe test facts (selected environment-presence sentinels, cwd, and additional
  directories) in `_meta`; never echo the whole environment or credential values.
- Model a delayed load consumer with a latch in the client-side test sink. The agent still obeys ACP
  and sends replay before its load response; the latch deterministically leaves typed replay
  unconsumed after the raw response has been observed. This proves the production load barrier,
  rather than adding a non-conformant agent delay or a timer.
- Add `tests/support/acp_runtime_subject.py`. It composes the real `SdkAcpEmployeeChild` and
  `AcpEmployeeRegistry`, records production sink/observer evidence, and exposes ACP-00 evidence for
  probes 1, 6, and 9. The existing `assert_load_replay`, `assert_durable_refresh`, and
  `assert_callback_wire_order` functions evaluate that evidence directly. For probe 1, the evidence
  positions are typed-consumer completion positions versus the public load/attach return position,
  so an early-ready implementation fails.
- Add `tests/support/acp_in_memory_binding_repository.py`, a locked async in-memory implementation
  of the exact resolver/CAS semantics below. It is real stateful proof, not pre-shaped evidence.
- Correct `tests/support/acp_reference_subject.py` without weakening `assert_load_replay`: every
  direct `session/load` uses the same production observer-slot/typed-consumer barrier before the
  subject records public load return. A consumer latch must prove the raw response can be observed
  while the reference load remains pending. This removes the reproduced scheduling race rather than
  hiding it with an extra wait after return.
- Add one malformed/future session-update switch during load. It must write a syntactically valid
  JSON-RPC notification that fails the pinned SDK `SessionNotification` model, followed by a valid
  load response. The production and reference barriers consume a visible protocol rejection and
  complete promptly; they never wait for a typed callback the SDK router suppresses.

Write the failing acceptance tests in the three named ACP-01 test files before implementing their
production subjects. Do not weaken or duplicate the ACP-00 assertions.

## 2. Ordered typed ingress and its failure rule

Add the only new tunable queue constant to `conversation/configuration.py`:

```text
ACP_SESSION_UPDATE_INGRESS_MAX_ITEMS = 1024
```

`ordered_ingress.py` owns one bounded sequence of slots per live child generation and one consumer
task. The synchronous observer handles only incoming `session/update` frames:

1. Validate `params` with the exact SDK `SessionNotification.model_validate` in strict alias mode.
2. Reserve the next wire-ordinal slot. For a valid model, record only a canonical fingerprint from
   the SDK's own alias serialization. For an invalid model, fill the slot immediately with the frozen
   `ProtocolUpdateRejectedPayload` and a display-safe discriminator/reason.
3. The typed SDK callback computes the same canonical fingerprint from its exact
   `SessionNotification` and fills the earliest unfilled matching reservation. Duplicate identical
   frames are matched by occurrence order. It never awaits the downstream sink.
4. A callback with no reservation, a second fulfillment, or a reservation/typed fingerprint mismatch
   is generation-fatal; it is never guessed into another slot.

The consumer waits for the head slot to be filled, then awaits the injected exact
`AcpConversationIngress` with either the typed `SessionNotification` or the frozen rejection. It
cannot skip a delayed valid callback to consume a later invalid/valid slot. Downstream calls never
overlap and completion order is raw wire order even if SDK callback tasks are scheduled later. The
implementation exposes reserved/fulfilled/consumed ordinals and conditions needed by the child load
barrier, not transcript data.

Overflow is generation-fatal and explicit:

1. The first full reservation set atomically closes ingress to new updates and records an
   `AcpSessionUpdateIngressOverflow` containing the queue limit and rejected notification identity.
2. It signals the child fatal-lifecycle event without awaiting inside the SDK callback. The child
   exits its process context, becomes not alive, and invokes the downstream death/error callback
   once with that overflow error.
3. Items already accepted by the queue retain their order and are drained while the downstream sink
   can make progress. The overflowing item is reported as rejected by the fatal error; it is never
   silently discarded or converted to text.
4. Every later callback for that ingress raises/reports a closed-ingress error and cannot reach the
   sink. Registry generation guards independently prevent any accepted stale item from mutating a
   replacement generation.

Normal close stops acceptance, drains accepted items, and stops the consumer. Cancellation during
bounded registry shutdown cancels the consumer and reports the unfinished shutdown rather than
hanging.

## 3. Official-SDK child and load-consumption barrier

Implement `SdkAcpEmployeeChildFactory` and `SdkAcpEmployeeChild` in `sdk_child.py` using only
`acp.stdio.spawn_agent_process` and its public `ClientSideConnection`/process values. The child
lifetime task keeps the returned async context open until close, process exit, ingress failure, or
task cancellation. Factory cancellation during partial spawn must unwind that context before
propagating, so a registry shutdown cannot orphan a process that was not yet published.

The SDK client callback object implements only the services present in this slice:

- `session_update` constructs the exact `SessionNotification` supplied by the SDK router and fulfills
  its observer reservation with no await in the callback body;
- `request_permission` delegates the exact `RequestPermissionRequest` to the injected callback when
  the backend declares permission support;
- an unexpected permission call for a definition that does not declare it fails as an ACP method
  error rather than inventing an answer;
- filesystem and terminal handlers are absent in ACP-01. A definition requesting either is rejected
  before spawn because ACP-02 has not supplied those services yet.

Build the initialization request with ACP protocol version `1`,
`Implementation(name="panels", title="Panels", version=planner.__version__)`, and
`ClientCapabilities` that advertise only actually composed filesystem/terminal services. For the
Hermes definition in this ticket both filesystem flags and terminal are false. Permission has no
separate ACP v1 client capability bit; its injected callback is still enforced from the backend
definition. Do not enable unstable client protocol mode.

Each frozen child operation checks `alive`, delegates by unpacking the exact SDK request model, and
returns the exact SDK response model:

- `initialize` validates response protocol `1`, non-null `agentInfo`, exact expected agent name and
  exact expected version, and `agentCapabilities.loadSession is True`. Any mismatch closes the
  candidate and raises a typed initialize-mismatch error before registry publication.
- `new_session`, `prompt`, and `cancel` are direct typed delegations.
- `load_session` is serialized by a child load lock and includes the barrier below.
- `close` is idempotent. It marks close intent before signalling the lifetime task, so a process
  exit caused by close settles the death callback with `None`; spontaneous exit or ingress failure
  settles it with the typed cause. One settlement guard permits exactly one callback in all races.

### Load barrier algorithm

The child installs one synchronous SDK `StreamObserver` and maintains monotonically increasing
connection-wide session-update frame and typed-consumption counts. Only one load may be armed at a
time.

1. Immediately before calling `ClientSideConnection.load_session`, arm a load epoch.
2. On the observer's outgoing `session/load` frame, record its JSON-RPC request ID. On every incoming
   `session/update` frame before the matching response, reserve the ordered slot described above. On
   the incoming response with the recorded request ID, freeze the target ordinal. The observer's only
   payload operation is the contract-authorized exact SDK validation/fingerprint or rejection; it
   never forwards a valid notification or interprets content.
3. Await the SDK `load_session` response. If it raises, abort the epoch and propagate; do not mint a
   session.
4. Await the ordered ingress consumer's completed ordinal reaching the frozen target. Completion is
   recorded only after the injected typed sink returns for valid notifications and protocol
   rejections alike. Child death, overflow, unmatched fulfillment, or shutdown wakes the waiter with
   its failure.
5. Return the exact `LoadSessionResponse` only after that condition. Notifications observed after
   the response are live traffic and do not extend the frozen target.

This uses no sleep, debounce, quiet period, raw update decoding, or transcript reconstruction. A
slow sink can delay readiness; it cannot reorder replay or let readiness overtake replay.

### Process, stderr, and death lifecycle

Start a stderr-drain task as soon as the SDK context yields the subprocess. Read chunks continuously
rather than calling an unbounded whole-stream read. Add the second named configuration bound:

```text
ACP_CHILD_STDERR_TAIL_MAX_BYTES = 65536
```

Retain only the last bytes up to that bound and decode snapshots with UTF-8
`errors="replace"`. Stderr never enters session ingress. A process wait task races the close and
ingress-fatal signals. Natural exit, including return code zero, is unexpected unless close intent
won first; its typed error includes return code and bounded diagnostic tail. On cancellation after a
registry deadline, the concrete lifetime kills the public subprocess handle, waits for it, cancels
stderr/ingress tasks, and exits the SDK context. This hard cancellation path exists only to complete
authorized shutdown; it does not restart the child.

## 4. Durable binding repository semantics

`employee_registry.py` accepts these internal async callables; they are not new browser or ACP wire
contracts:

```text
resolve_binding(employee_id) -> ConversationSessionBinding | None
compare_and_swap_binding(expected_binding | None, candidate_binding)
    -> ConversationSessionBinding
```

CAS compares the complete expected value, including employee, ACP session, backend, and binding
generation. When it writes, it returns the candidate. When expected is stale, it returns the actual
durable winner without writing. Storage failure raises. The in-memory test repository protects this
operation with one lock and validates candidate/winner employee identity.

Binding rules are exact:

- Resolver output with the wrong employee ID fails before spawn.
- For every spawn/new/load, resolve cwd through the selected
  `AgentBackendDefinition.working_directory_for(employee)`. Build `additionalDirectories` from the
  declared workspace roots in their original order, excluding the selected cwd when it is one of
  them; if the backend selects another absolute cwd, retain every declared root. Generic code never
  hard-codes the Hermes first-root policy. Session requests pass an explicit empty MCP-server list.
- With no binding: initialize the requested backend child, call `session/new` with that selected cwd
  and additional-root list, construct binding generation `1`, CAS against `None`, then publish only
  the durable winner.
- With a same-backend binding: initialize, call `session/load` for that exact ACP session and the
  same cwd/additional-root mapping, pass the consumption barrier, then publish that unchanged
  binding.
- With a different-backend binding: never load its session through the requested backend. Initialize
  the requested backend, create a new session, and CAS a candidate whose binding generation is
  exactly old + 1.
- A child crash never changes ACP session ID or binding generation. The next demand spawns a new
  child generation and loads the durable binding. A load failure closes that child and propagates;
  it never falls back to `session/new`.
- If candidate persistence raises, close the candidate and publish nothing. The next demand resolves
  the still-durable prior value.
- If CAS returns another winner, close the losing candidate generation, validate that winner, and
  adopt it through a fresh child generation before publication. Resolve the child's definition from
  the winner's `backend_key` (using an otherwise identical `ConversationEmployee`) and load the
  winner only through that backend. The normal concurrent-winner case already has the requested
  backend. If an external writer selected another backend concurrently, the current demand still
  returns the durable winner; a later demand whose selected backend differs performs the normal
  explicit backend transition.

CAS adoption is not a retry loop. It preserves all three hard rules: the durable store wins, every
winner is loaded before publication, and no backend loads another backend's session identity.

## 5. Async employee registry lifecycle

Implement `AcpEmployeeRegistry` with a short global `asyncio.Lock`, one per-employee
publication/update `asyncio.Lock`, a monotonically increasing in-memory child-generation counter,
one initialization task per employee, and one new-conversation task per employee. Backend
definitions are supplied through one keyed mapping/factory; generic modules never branch on a
backend key.

`get_or_spawn(employee)` works as follows:

1. Reject after registry closing begins. Return the current record only when its child is alive and
   its employee/backend selection still matches.
2. Coalesce callers for one employee onto the same initialization task. Allocate a new child
   generation before factory creation. Different employees receive independent tasks and can
   initialize concurrently.
3. Resolve and validate the binding, then run the matching new/load/backend-transition algorithm
   above entirely outside the registry lock.
4. Before publication, acquire the employee publication/update gate and then briefly the global
   registry lock. Re-check registry openness, current child generation, child aliveness, and the exact
   persisted binding; publish as one atomic record replacement. Losing/closing candidates are torn
   down. The lock order is always employee gate then global lock.

The registry closes generation over both injected callbacks. The conversation-ingress wrapper
acquires the employee gate, briefly checks the captured generation under the global lock, releases
the global lock, then awaits the domain sink while retaining the employee gate. Publication of N+1
therefore waits for every admitted N sink to return. A callback that acquires the gate after N+1 is
visible fails its generation check and never enters the sink. The death wrapper uses the same lock
order before retiring a matching record. No global lock is held across user code. Unexpected death
causes no background respawn; the next explicit demand owns recovery.

`attach(employee)` is the transcript-ready entry point for later composition. It coalesces first
demand, uses `session/load` for an existing durable session, and does not treat the load response as
history. Concurrent attaches for the same binding join one load wave; after the live child already
exists, a later attach starts a new load wave so refresh receives typed replay. For a just-created
empty session, the first attach may become ready after durable publication without a redundant
empty load. Every attach that targets pre-existing history or crash recovery passes the SDK
load-consumption barrier before returning.

`new_conversation(employee)` uses the same one-task-per-employee coalescing rule, so simultaneous
requests share one replacement rather than serially creating several. It:

1. obtains the current live record and exact old binding;
2. creates a new ACP session on that current child;
3. CASes `old -> candidate` with generation old + 1;
4. publishes the new binding only if persistence won and the child generation is still current;
5. on CAS loss, closes that candidate child and adopts/loads the returned winner through a fresh
   child generation.

A later, non-overlapping new-conversation request deliberately creates another generation. No Chat
row, event row, transcript row, or worker-turn row participates in binding resolution or CAS.

### Shutdown

`shutdown(deadline)` first marks the registry closing and rejects new demand. Under the short lock it
snapshots all live records plus initialization/new/load tasks, then outside the lock:

- cancels partially initialized and rebind tasks;
- closes every unique child concurrently against the same absolute monotonic deadline;
- closes ingress and stderr tasks as part of each child lifetime;
- waits only for the shared remaining budget.

On deadline expiry it force-cancels the remaining local lifetime/process contexts, records the
unfinished employee IDs, and raises `AcpEmployeeRegistryShutdownError`. It does not start a new
budget per child and never waits forever. Child and registry settlement guards ensure shutdown,
process exit, and a concurrent overflow cannot duplicate the death callback.

## 6. Hermes backend definition and environment

Implement one `build_hermes_acp_backend_definition` in `hermes_backend.py`. It accepts absolute
`hermes_executable`, `hermes_home`, and `hermes_source_root` paths plus the injected Hermes
`BackendTurnStrategy`, and returns:

- `backend_key="hermes"`;
- `argv=(str(hermes_executable), "acp")`, where the executable basename is `hermes`;
- expected agent `hermes-agent`, exact version `0.18.2`;
- `BackendTurnCapabilities(supports_steer=True, observes_compaction=True)`;
- `ReverseServiceCapabilities(filesystem=False, terminal=False, permission=True)`;
- first workspace root as cwd;
- ordered inherited environment names exactly `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and
  `USER` (copied only when present);
- explicit `HERMES_HOME` and `HERMES_PYTHON_SRC_ROOT` overrides from the supplied paths.

The pinned SDK starts every subprocess from its public `default_environment()` before overlaying the
provided mapping. The generic factory computes that actual default mapping before spawn and fails
closed if any present default name is absent from the selected definition's declared inherited-name
set. This makes every backend explicitly accept the SDK floor instead of silently receiving it. It
then creates a fresh subprocess mapping rather than copying ambient `os.environ`: copy only
definition-allowlisted names, apply explicit definition overrides, scrub every assembled `PLAN_*`
value and `HERMES_TUI_SKILLS` regardless of its source, then set Panels identity:

- ticket: `PLAN_TICKET_ID=<entity_id>`, `PLAN_ACTOR=worker`;
- agent: no `PLAN_TICKET_ID`, `PLAN_ACTOR=chief`.

`HERMES_HOME` exists only because the definition explicitly supplies it. Ambient provider keys,
ambient `HERMES_HOME`, stale ticket/actor values, and `HERMES_TUI_SKILLS` remain absent. The ACP
SDK's own default subprocess environment currently supplies the same six POSIX names; Hermes
declares them. Tests pollute the parent environment with safe sentinel provider/identity values,
prove they do not reach the scripted child, and use a fake definition omitting one present SDK
default to prove fail-before-spawn behavior.

Hermes-specific strings, capabilities, and environment variables appear only in
`hermes_backend.py` and its tests. Add a source/AST boundary test that `sdk_child.py`,
`ordered_ingress.py`, and `employee_registry.py` contain no conditional on `"hermes"`.

## Acceptance-to-test map

| Named acceptance | Exact proof |
| --- | --- |
| 1. SDK child lifecycle | `tests/unit/test_acp_employee_child.py`: exact request/response delegation; protocol/name/version/load-capability rejection; backend-resolved cwd/additional roots; explicit empty MCP list; confined environment including SDK-default fail-closed; bounded replacement-decoded stderr; expected/unexpected death; prompt/cancel; idempotent close |
| 2. Ordered ingress | same file: observer reservation and typed fulfillment; invalid replay rejection; callback returns while sink latch is blocked; max concurrent sink call is one; exact Hermes-shaped live/replay order; thought remains `AgentThoughtChunk`; observer response precedes consumer but public load does not; bounded burst; explicit fatal overflow; accepted-prefix no-drop; unmatched/later/stale callback rejection |
| 3. Registry and bindings | `tests/unit/test_acp_employee_registry.py`: per-employee coalescing/independence; initial generation 1; crash reload unchanged; failed load no remint; new/backend-change +1; CAS winner/loser; persistence teardown; N-sink/N+1-publication gate; late update/death guards; partial-init and shared-deadline shutdown |
| 4. Hermes definition | `tests/unit/test_hermes_acp_backend.py`: exact builder fields, argv, identity/version/capabilities, first-root cwd, six-name inheritance, explicit Hermes overrides, polluted ambient scrubbing, injected strategy identity, and generic-module no-Hermes conditional |
| 5. ACP-00 production probes | `tests/support/acp_runtime_subject.py` plus registry tests run existing probe assertions 1/6/9; `mutate_probe_evidence` early-ready, binding-drift, and callback-reorder mutations each fail its matching assertion |
| 6. Focused gates | commands below and one independent settled-diff review; the orchestrator alone runs canonical `./verify` afterward |

Additional required race cases are explicit tests, not incidental coverage:

- process exit versus `close` versus ingress overflow settles one death callback;
- a response-observed but sink-blocked load remains pending;
- malformed replay is consumed as a rejection and cannot hang load;
- child N paused inside the sink delays N+1 publication; after publication no N callback enters;
- the direct ACP-00 reference subject records load return only after the same barrier;
- a fake backend selecting the second workspace root drives process/new/load cwd and additional roots;
- a fake definition omitting a present SDK-default environment name fails before spawn;
- two simultaneous `new_conversation` calls produce one persisted replacement;
- shutdown while factory creation, initialize, load, and stderr flood are each in progress ends
  within one shared deadline.

## TDD implementation order

1. Add the Hermes-shaped fixture and scripted opt-in switches; keep all ACP-00 tests green.
2. Write Hermes-definition/environment tests, then implement `hermes_backend.py` and environment
   construction without spawning real Hermes.
3. Write slot-reservation/typed-match/rejection/serial-consumer/overflow and
   response-before-consumption tests, then implement `ordered_ingress.py` and the observer ordinal
   barrier. Correct the direct ACP-00 reference subject with the same primitive.
4. Write SDK delegation, identity mismatch, stderr, death-race, and close tests, then implement
   `sdk_child.py` around `spawn_agent_process`.
5. Write binding/CAS/generation-gate/new-conversation/shutdown tests against fake children and the
   real in-memory repository, then implement `employee_registry.py`.
6. Compose the production ACP-01 conformance subject and run probes 1, 6, and 9 plus the three
   targeted mutations. Do not add a second assertion vocabulary to make them pass.
7. Run the focused checks once the slice is settled; address the independent plan review before
   implementation and the independent implementation review before handoff.

## Focused commands

Do not install new dependencies and do not run `./verify` from the implementation sub-agent.

```sh
.venv/bin/ruff check src/planner/conversation tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/unit/test_acp_employee_child.py tests/unit/test_acp_employee_registry.py tests/unit/test_hermes_acp_backend.py tests/support/acp_*.py
.venv/bin/mypy src/planner/conversation
.venv/bin/pytest tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/unit/test_acp_employee_child.py tests/unit/test_acp_employee_registry.py tests/unit/test_hermes_acp_backend.py
node web/tests/acp-contracts.test.mjs
npm --prefix web run check
npm --prefix web test
```

Record full focused output in this ticket directory. The implementation review should concentrate on
observer reservation/typed matching/rejection, reference and production load barriers, CAS
publication order, publication/update gate, child-vs-binding generation guards, SDK-default
environment confinement, backend cwd, overflow no-silent-drop behavior, stderr/process teardown,
and the absence of Hermes branches from generic runtime modules.
