# ACP-01 — generic ACP child runtime and Hermes definition

Contract-scoped production-runtime ticket for `orchestration/acp-migration/plan.md`. ACP-00 is the
frozen public contract and conformance foundation. Read the owner brief, `research.md`, the reviewed
program, ACP-00's contract/reviews, `PRINCIPLES.md`, and `AGENTS.md` before planning. This ticket adds
an uncomposed server-side ACP runtime. It does not add a route, turn broker, reverse filesystem or
terminal service, database migration, Svelte component, CSS, or legacy-path cutover.

## Outcome

Panels can own one official-SDK ACP agent child per `ConversationEmployee`, initialize it, create or
load that employee's durable ACP session, consume typed updates in wire order, survive a child crash
without changing the durable binding, create a deliberately fresh conversation with compare-and-swap
binding publication, and shut down without leaking children or ingress tasks. A backend definition
for pinned Hermes 0.18.2 supplies `hermes acp`, environment policy, cwd policy, declared capabilities,
and expected initialize identity. Nothing is yet reachable from HTTP or the browser.

## Frozen sources and contracts

- Implement `AcpEmployeeChild`, `AcpEmployeeChildFactory`, `AcpConversationIngress`,
  `ConversationEmployee`, `ConversationSessionBinding`, and `AgentBackendDefinition` exactly as frozen
  by ACP-00 plus the reviewed correction below. Do not add a second request/update model or change an
  unrelated public field.
- Use `acp.stdio.spawn_agent_process` and its returned `ClientSideConnection`; do not implement
  JSON-RPC, parse stdout as a second transport, or call private SDK methods.
- Use ACP protocol version 1 and a client `Implementation` identifying Panels. Advertise only the
  reverse services declared by the backend and actually supplied to the child. ACP-02 supplies the
  production permission/filesystem/terminal brokers; ACP-01 uses injected callbacks in tests.
- The Hermes checkout at `~/.hermes/hermes-agent` is read-only. No writes or git commands there.

## Production components and ownership

Implement purpose-named modules below `src/planner/conversation/`:

1. **SDK child.** A concrete child/factory keeps the SDK async context open for the child lifetime,
   drains stderr from process start, exposes only the frozen typed lifecycle, watches process death,
   validates the initialize response against the selected backend definition, and settles its death
   callback exactly once. Intentional close is idempotent and is distinguishable from unexpected
   death. The factory builds the subprocess environment from the backend's explicit inherited-name
   allowlist plus explicit overrides and Panels employee identity; it never passes ambient `PLAN_*`
   or provider credentials that the definition did not name.
2. **Ordered ingress.** The synchronous SDK stream observer reserves one bounded ingress slot for
   every raw `session/update` in wire order. It may strictly validate only that notification through
   the exact SDK `SessionNotification` model: a valid frame records a canonical SDK-serialization
   fingerprint and is filled only by the later typed SDK callback; an invalid frame fills its slot
   with the frozen display-safe `ProtocolUpdateRejectedPayload`. One task per live child generation
   awaits the downstream `AcpConversationIngress` serially. No callback task reduces, broadcasts, or
   calls product writers. A full queue, unmatched typed callback, or duplicate fulfillment is fatal
   to that generation: no update is silently dropped, the child is retired, the downstream
   death/error path is notified once, and later callbacks from that generation are rejected.
3. **ACP employee registry.** A conversation-domain async registry owns one record per employee and
   coalesces concurrent first demand. It may reuse the proven lifecycle decisions from
   `EmployeeChildRegistry`, but must not make an ACP connection implement the legacy synchronous
   raw-frame `ChildReader` interface and must not add ACP branches to the legacy registry. Backend
   definitions are resolved through one injected keyed registry; generic runtime code contains no
   `if backend_key == ...` logic.
4. **Binding repository seam.** The registry receives injected resolve and compare-and-swap
   functions expressed in the frozen `ConversationSessionBinding` value. ACP-01 tests use a real
   in-memory implementation. ACP-04 will compose the product database writers/migration. The runtime
   does not read or write current Chat tables, Ticket fields, or agent-session tables directly.
5. **Hermes definition.** A single definition builder returns backend key `hermes`, argv ending in
   `hermes acp`, expected initialize agent `hermes-agent` version `0.18.2`, cwd at the first declared
   workspace root, `supports_steer=True`, `observes_compaction=True`, and reverse capabilities
   `filesystem=False`, `terminal=False`, `permission=True`. It accepts the concrete Hermes executable,
   Hermes home/source-root overrides, and the injected Hermes turn strategy; ACP-02 implements the
   strategy behavior. It declares every inherited environment name it needs and no provider/UI
   styling behavior.

Panels employee identity added by the generic factory is exact:

- ticket employee: `PLAN_TICKET_ID=<entity_id>` and `PLAN_ACTOR=worker`;
- top-level agent employee: no `PLAN_TICKET_ID` and `PLAN_ACTOR=chief`.

Any ambient `PLAN_TICKET_ID`, `PLAN_ACTOR`, `HERMES_HOME`, or provider credential is scrubbed unless
the selected definition deliberately supplies it. `HERMES_TUI_SKILLS` remains absent. This identity
policy is common Panels worker context, not a Hermes-name conditional.

## Child generation and binding generation are different

- **Child generation** is in-memory process ownership. It increments on every spawn/respawn and
  generation-guards child callbacks, death, and ingress. A late update or death from generation N
  cannot mutate or retire generation N+1.
- **Binding generation** belongs to `ConversationSessionBinding`. It starts at 1 when the first ACP
  session is successfully persisted and advances by exactly one only when ACP session identity is
  deliberately replaced (new conversation or backend change). A child crash/respawn/load keeps the
  same ACP session ID and binding generation.
- A stored binding whose employee ID is wrong fails closed. A stored binding for a different backend
  causes an explicit new-session binding transition; a backend never attempts to load another
  backend's session ID.
- A candidate binding is persisted by compare-and-swap before the registry publishes it. Persistence
  failure closes the candidate child. If another caller won the CAS, the losing candidate is never
  published; the registry adopts and loads the winner through a fresh child generation.
- `new_conversation` is serialized per employee. It creates a new ACP session on the current child,
  compares against the exact old binding, and publishes only the persisted winner. A stale concurrent
  new request adopts the already-new winner rather than creating a second published replacement.

`AcpConversationIngress` is the one ordered downstream port for exact
`SessionNotification | ProtocolUpdateRejectedPayload`. Valid payloads still come only from the SDK's
typed callback; the observer never forwards or interprets a valid notification. Its narrow duplicate
SDK validation exists only to reserve/match order and turn a router-rejected raw frame into the
already-frozen visible rejection instead of an unbounded wait.

The binding store owns durability; the registry owns live child/process identity. No browser row,
Chat transcript row, event row, or worker-turn row is a session binding.

## Attach/load completion barrier

`attach` and crash recovery use `session/load`, never `session/new` and never an arbitrary quiet
period. A load response is not transcript data. Because the SDK may schedule notification callbacks
concurrently, `load` is complete only after every reserved session-update slot observed before that
load response has passed through the single ordered consumer. Valid slots must first be fulfilled by
their exact typed callback; invalid slots carry one `ProtocolUpdateRejectedPayload`. The observer may
use strict SDK-model validation and canonical SDK serialization only for reservation/matching and
rejection; it must not forward a valid payload, interpret content, or become a second JSON-RPC path.
Typed callbacks remain the only valid-notification payload source.

Live updates arriving after the response remain ordinary ordered ingress. A slow downstream sink
may delay load readiness but cannot reorder replay, announce readiness early, or turn thought into
assistant text.

## Lifecycle rules

- Concurrent `get_or_spawn`/`attach` calls for one employee create one child and one session.
  Different employees may initialize independently.
- Each employee has a publication/update gate separate from the short global registry lock. An
  admitted generation-N sink call holds that gate through its awaited downstream mutation. Publishing
  N+1 waits for every admitted N call to leave, then advances the generation under the same gate.
  Thus no N sink can complete after N+1 becomes visible; the global registry lock is never held across
  user code.
- First demand resolves a binding. With none, initialize → new session → persist generation 1 →
  publish. With one, initialize → load the exact session → complete the load barrier → publish ready.
- Initialize protocol/name/version mismatch closes the child and publishes nothing. Backend version
  validation is exact for this pinned ticket; later pin changes update one backend definition and its
  conformance evidence.
- Unexpected child exit marks only the matching child generation dead and closes its ingress. The
  durable binding remains. Next demand spawns and loads it. The registry does not spin-restart an idle
  child and does not silently mint a replacement when load fails.
- Calls on a dead/stale generation fail visibly. No prompt can be submitted through a record after
  its death has won the generation guard. ACP-02 owns active-prompt policy and receipts.
- Registry shutdown rejects new demand, closes all live and partially initialized children
  concurrently within the supplied deadline, drains/cancels ingress and stderr tasks, and invokes no
  duplicate death callback. Deadline expiry force-cancels the remaining local tasks/process contexts
  and returns a typed/raised shutdown failure; it never hangs.
- Child stderr is continuously drained so an agent cannot deadlock on a full pipe. Keep only a named,
  bounded diagnostic tail with replacement decoding. Stderr is never a transcript update or browser
  assistant message.

## Scripted and Hermes-shaped proof

Extend the ACP-00 scripted agent only with deterministic switches needed by this ticket: initialize
identity mismatch, delayed callback execution after load response, stderr flood, high-volume update
burst, controlled slow consumer, a malformed/future replay notification, deterministic process
death, and stale post-death callback. All
normal protocol traffic still uses the official SDK; no test reads the Panels database or Hermes
checkout.

Hermes-shaped replay fixtures must include thought, assistant content, missing message IDs, tool
start/progress, plan replacement, available commands, usage, and session provenance metadata in the
same typed discriminators observed from Hermes. The production runtime need not interpret those
updates in ACP-01; it must preserve their exact typed order through the downstream sink. The existing
ACP-00 conformance assertions 1 (load replay), 6 (durable binding), and 9 (callback order) must run
against the production ACP-01 subject rather than a lookalike assertion.

## Allowed files

- `src/planner/conversation/**`
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_hermes_acp_backend.py`
- `tests/support/acp_*.py`
- `tests/fixtures/acp/**`
- `orchestration/tickets/acp-01-child-runtime-hermes/**`

The plan may choose fewer test files. Any additional production or test path requires orchestrator
approval before implementation and a written reason in the plan.

## Must not touch

Routes, `core/server.py`, current config schema/YAML, database schema/migrations, runtime composition,
existing `chat/`, `tickets/`, `minds/`, or `hermes_backend/` production code, the legacy
`EmployeeChildRegistry`, current Chat/relay tests, browser wire shapes, `web/`, CSS/assets, docs,
`verify`, dependency pins, and the Hermes checkout. Do not edit an existing non-ACP test to pass.

## Named acceptance

1. Official-SDK child tests prove exact initialize/new/load/prompt/cancel/close delegation,
   protocol/name/version validation, explicit environment confinement/employee identity, cwd and
   additional-root mapping, continuous bounded stderr drain, expected/unexpected exit distinction,
   and idempotent close.
2. Ordered-ingress tests prove observer reservation plus typed fulfillment, one serial consumer,
   exact Hermes-shaped live and replay order, prompt visible rejection for malformed/future replay,
   load readiness only after valid/rejected slot consumption, no thought flattening, bounded
   slow-consumer behavior, fatal no-drop overflow, unmatched-callback failure, and stale-generation
   rejection.
3. Registry tests prove one child per employee, concurrent initialization coalescing, independent
   employees, first binding generation 1, crash/reload with unchanged binding, failed-load no-remint,
   new-conversation generation advance, backend-change advance, CAS loser adoption, persistence
   failure teardown, generation-guarded late callbacks/death, an N sink paused across an N+1
   publication attempt, partial-initialize shutdown, and bounded all-child shutdown.
4. Hermes-definition tests prove exact key/argv/identity/version/capabilities, first-root cwd,
   required explicit Hermes environment, absence of ambient identity/provider leakage, injected
   strategy ownership, and no backend-name conditional in generic runtime modules.
5. The production subject passes ACP-00 conformance probes 1, 6, and 9. Targeted mutations (early
   load-ready, binding drift on respawn, callback reorder) each fail the corresponding assertion.
6. Focused Ruff, strict Mypy over `src/planner/conversation`, all ACP-00 and ACP-01 Python tests, the
   existing TypeScript ACP contract test, Svelte check, and the full web test script pass. One
   independent sub-agent reviews the settled implementation diff; the orchestrator then runs one
   canonical `./verify` at the checked-in relay default.

## Review and integration

One implementation sub-agent writes a concrete plan with an acceptance-to-test map before editing
source. One different sub-agent performs a focused plan review against this contract, ACP-00, the
official SDK, and the reviewed program. After disposition, one implementation sub-agent works only
the allowed files. One different sub-agent performs the implementation review. A second review round
is used only for a concrete unresolved blocker or a material load-bearing correction.

The orchestrator spot-checks the load barrier, binding CAS, generation guards, stderr/process
shutdown, and generic-vs-Hermes boundary before the canonical gate. ACP-01 is done only when the
named tests pass and `./verify` ends `VERIFY: PASS`.
