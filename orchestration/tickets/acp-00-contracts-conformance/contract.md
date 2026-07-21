# ACP-00 — conversation contracts, pinned typed donor, and conformance harness

Contract-scoped foundation ticket for `orchestration/acp-migration/plan.md`. Read the owner brief,
`research.md`, the reviewed program, `PRINCIPLES.md`, and the repository `AGENTS.md` before planning.
This ticket freezes public shapes and reusable proof machinery. It adds no production route, child,
broker, composition, or visible UI.

## Outcome

Panels has one typed contract package for its ACP conversation system, exact server/browser
dependency pins, the framework-free transcript-state donor at a reproducible source revision, and a
scripted conformance harness that later runtime/backend tickets must instantiate. ACP SDK models stay
authoritative for ACP payloads: no Panels-owned copy of the ACP unions is permitted.

## Locked dependency and donor pins

- Python: `agent-client-protocol==0.11.0` in both install metadata and the fully pinned runtime
  requirements. Use its `acp.stdio.spawn_agent_process`, `ClientSideConnection`, generated Pydantic
  schema, and `acp.utils.serialize_params`; do not add a JSON-RPC implementation.
- TypeScript protocol types: `@agentclientprotocol/sdk==1.2.1`, the exact dependency used by the
  donor revision. Pin its lockfile resolution rather than a range. Its generated `SessionUpdate`,
  content, tool, plan, usage, command, and permission types remain the browser authority.
- Typed state donor: source revision `525a9d83c5ace577ac0417bf82bf983da4042663` from
  `github.com/zvzuola/acp-components`, package `packages/core`. Vendor the minimal executable state
  slice under `web/src/vendor/acp-components-core/`: `src/types/index.ts`,
  `src/store/sessionStore.ts`, `src/utils/id.ts`, and `src/transport/types.ts`. Preserve their source
  paths beneath that directory. The slice uses exact `zustand==5.0.13`, the donor lock resolution.
  Do not vendor its ACP client, browser transports, skills extension, React views, examples, or CSS.
- Add `UPSTREAM.md` with repository URL, exact commit, copied paths, copy date, upstream file SHA-256
  values, declared MIT license evidence, and every intentional local correction. Add a standard MIT
  `LICENSE` for the vendored slice, noting that the upstream package declares MIT but did not contain
  a standalone license file at the pin. Source-derived corrections after ACP-00 must be recorded in
  `UPSTREAM.md`; ACP-00 itself copies the four source files byte-for-byte.
- Do not install the stale `@acp-components/core` npm package and do not add React. `acp-ui` contributes
  no source or styling.

## Frozen Python public contracts

Create `src/planner/conversation/` with `__init__.py`, `contracts.py`, `backend_contracts.py`,
`wire_contracts.py`, and `configuration.py`. Public values are immutable (`frozen` Pydantic models or
frozen dataclasses as appropriate), use descriptive field names, reject invalid enum/literal values,
and expose no untyped public `dict[str, object]` payload.

### `contracts.py`

- `ConversationEntityKind = Literal["ticket", "agent"]`. Day chat is not part of the destination.
- `ConversationEmployee`: `employee_id`, `entity_kind`, `entity_id`, ordered absolute
  `workspace_roots`, and `backend_key`. It rejects an empty ID/backend/root set, relative roots, and
  duplicate roots.
- `ConversationSessionBinding`: `employee_id`, `acp_session_id`, `backend_key`, and positive
  `binding_generation`.
- `ConversationActivityState`: `connecting | loading | idle | thinking | working | compacting |
  waiting_for_permission | interrupted | failed`.
- `ConversationActivity`: state, non-empty display-safe `detail`, and positive `sequence`.
- `TurnDeliveryChoice`: `normal | steer | send_now | queue`.
- `TurnDeliveryReceiptState`: `accepted | queued | started | interrupted | rejected`.
- `TurnDeliveryReceipt`: `client_message_id`, choice, state, optional positive `queue_position`, and
  optional display-safe rejection/interruption `reason`. Queue position is present only for queued
  state; reason is required only for rejected state.
- `QueuedPrompt`: `client_message_id`, the exact SDK `PromptRequest` (not a copied content-block
  union), positive `enqueue_sequence`, and integer `enqueued_at`.
- `ContextCompactionState`: `compacting | compacted | failed`; trigger `explicit | automatic`.
- `ContextCompaction`: stable `boundary_id`, state, trigger, and optional display-safe failure
  `reason`. Reason is required only when failed. Backend compaction context is private and is never a
  public payload field.
- `ConversationPermissionLifecycle`: `pending | answered | cancelled`.
- `ConversationPermissionRequest`: stable Panels `request_id`, `employee_id`, `backend_key`, the
  exact SDK `RequestPermissionRequest`, lifecycle, integer `deadline_at`, and positive
  `opened_sequence`. The embedded request is the sole owner of ACP session ID, tool-call data, and
  exact ordered `PermissionOption` values. ACP v1 options have `optionId`, `name`, and `kind`; there
  is no separate scope field and Panels invents none.
- `ConversationPermissionOutcome`: `request_id`, exact SDK `RequestPermissionResponse`, optional
  display-safe `cancellation_reason`, and positive `settled_sequence`. A selected outcome preserves
  the exact agent option ID. A cancelled outcome carries a reason; neither creates a ticket status.

### `configuration.py`

Freeze `CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS = 300`. This is the only permission-response
timeout default. Later composition may inject an override for tests/configuration; no second magic
number is allowed.

### `backend_contracts.py`

- `BackendTurnCapabilities`: `supports_steer` and `observes_compaction`. Queue is a Panels FIFO and
  Send Now is ACP cancel/settle/start, so neither is a backend capability. Agent-advertised `/queue`
  is only a slash command.
- `ReverseServiceCapabilities`: `filesystem`, `terminal`, and `permission`; a backend definition may
  request only services the client composes and advertises.
- `AgentBackendDefinition`: `backend_key`, exact executable `argv`, inherited environment-name
  allowlist, explicit environment overrides, expected agent name/version, the two capability values,
  a working-directory resolver from `ConversationEmployee`, and a `BackendTurnStrategy`.
- `BackendTurnStrategy` protocol: declared native steer operation plus compaction observation/capture.
  Unsupported steer must return a typed rejection; implementations cannot infer behavior from a
  backend name.
- `AcpSessionUpdateIngress`: async enqueue-only callback accepting the exact SDK
  `SessionNotification`.
- `AcpEmployeeChild` protocol: positive generation/alive identity plus async
  initialize/new/load/prompt/cancel/close operations expressed with the exact SDK request/response
  models. `session/load` returning is distinct from its preceding update callbacks.
- `AcpEmployeeChildFactory` protocol: creates one child for a `ConversationEmployee` and generation,
  with typed update, permission, and death callbacks. The concrete SDK connection is ACP-01.

Protocols may introduce small named callback result contracts if strict typing requires them, but
they may not add transport behavior or new public concepts.

## Frozen browser wire

`wire_contracts.py` defines a discriminated Pydantic union. Every server envelope has
`wire_version=1`, employee/entity/session identity, positive binding generation, and a positive,
monotonic per-session `sequence`. It contains one typed payload from this closed set:

1. `acp_session_update`: the exact SDK `SessionNotification`; its `sessionId` must match the outer
   session and serialization uses SDK aliases with defaults/`None` excluded.
2. `activity`: `ConversationActivity`.
3. `delivery_receipt`: `TurnDeliveryReceipt`.
4. `queue_snapshot`: ordered `QueuedPrompt` items.
5. `context_compaction`: `ContextCompaction`.
6. `permission_request`: `ConversationPermissionRequest`.
7. `permission_outcome`: `ConversationPermissionOutcome`.
8. `connection`: `reset | ready | closed | error`, plus display-safe detail and required
   `supportsSteer`; reset carries the new binding generation. The capability is never inferred from
   commands, backend identity, or display text.
9. `protocol_update_rejected`: rejected ACP `sessionUpdate` discriminator (or `missing`), display-safe
   reason, and the persistent user-facing status **Agent sent an unsupported update**. Raw protocol
   detail belongs only in diagnostics.
10. `human_echo`: client message ID and exact SDK `PromptRequest`.
11. `terminal_state`: Panels terminal lifecycle plus the exact SDK `TerminalOutputResponse`; terminal
    output stays typed tool state and never becomes assistant prose.

Browser actions are one closed discriminated union:

- `attach`: employee ID and optional last-seen generation/sequence;
- `prompt`: employee ID, client message ID, exact SDK `PromptRequest`, and delivery choice;
- `cancel`: employee ID and optional queued client-message ID (`None` targets the active prompt);
- `new_conversation`: employee ID;
- `permission_response`: employee ID, Panels request ID, and exact agent-supplied option ID.

There is no free-form `data`, flattened reasoning, transcript snapshot, backend-name field, canonical
resource invalidation, or browser-to-agent JSON-RPC message. An unknown/partial agent session update
fails closed into `protocol_update_rejected`; an invalid browser action receives a typed validation
error/receipt and never changes agent state. Neither case becomes assistant text.

Create `web/src/lib/acp/contracts.ts` to mirror only the Panels envelope/action layer and import all
ACP unions from `@agentclientprotocol/sdk`. It must be structurally checked against canonical JSON
fixtures emitted by the Python contracts; it may not hand-copy ACP unions.

## Scripted conformance harness

Create a real newline-delimited stdio scripted ACP agent under `tests/support/` using the official
Python SDK's agent-side connection. It writes diagnostics to stderr only, stores sessions in memory,
supports new/load/prompt/cancel, can issue permission reverse calls, and exposes deterministic script
switches for delayed/concurrent callbacks, missing message IDs, automatic compaction provenance,
partial/unknown updates, cancellation, and process death. It must never read the Panels database or
Hermes checkout.

Create reusable conformance helpers and canonical JSON fixtures under `tests/support/` and
`tests/fixtures/acp/`. A manifest names all ten mandatory probes and the ticket that completes each
production assertion. ACP-00 runs every probe against a scripted reference subject; later adapter
and browser tickets call the same helpers/fixtures rather than rewriting lookalike tests:

1. load replay finishes before the load response;
2. live/replayed thought stays typed and never assistant text;
3. stable message IDs and deterministic missing-ID turn fallback group correctly;
4. plan snapshots replace and tool progress reconciles by tool-call ID;
5. permission cancel/death/disconnect/timeout settles exactly once;
6. refresh targets the same durable employee session/binding generation;
7. steer is real or visibly unavailable; common Queue and Send Now remain broker-owned;
8. explicit/automatic compaction has visible started/completed lifecycle or exact failure, with no
   backend context in the public payload;
9. concurrent SDK callbacks reduce/broadcast in wire order;
10. unknown/partial replay yields `protocol_update_rejected`, never text fallback.

The scripted subject may implement a minimal in-test reducer/broker sufficient to prove the harness
itself. That implementation stays under `tests/support/` and is not production runtime. There are no
skips, xfails, empty test bodies, network-dependent tests, quiet-period heuristics, or real-agent
requirements in ACP-00.

## Allowed files

- `requirements.txt`, `pyproject.toml`
- `src/planner/conversation/**`
- `tests/unit/test_conversation_contracts.py`
- `tests/unit/test_acp_conformance_harness.py`
- `tests/support/acp_*.py`
- `tests/fixtures/acp/**`
- `web/package.json`, `web/package-lock.json`
- `web/src/vendor/acp-components-core/**`
- `web/src/lib/acp/contracts.ts`
- `web/tests/acp-contracts.test.mjs`

The implementation plan may split the two named Python test files, but any additional file must stay
inside the listed source/test directories and be justified before implementation. Touch nothing else.

## Must not touch

All routes, `core/server.py`, runtime composition, database/schema/migrations, existing chat or
Hermes-backend code, `EmployeeChildRegistry`, legacy tests, Svelte components, CSS/assets, docs,
config, `verify`, and the Hermes checkout. Do not edit an existing test to make it pass. Do not run
git commands in `~/.hermes/hermes-agent`.

## Named acceptance

1. Contract construction/validation/immutability tests cover every literal and cross-field invariant,
   SDK alias serialization, exact permission-option preservation, prompt blocks without copied unions,
   matching inner/outer session IDs, and fail-closed unknown updates/actions.
2. Dependency/provenance tests assert the exact Python/TypeScript/Zustand pins, donor revision, copied
   path hashes, MIT declaration note, and absence of stale core/React/acp-ui dependencies.
3. The scripted ACP process proves protocol-only stdout, stderr diagnostics, initialize/new/load/
   prompt/cancel, typed replay-before-response, permission reverse calls, and deterministic death.
4. The reusable manifest contains exactly the ten probes above; the scripted reference subject passes
   every probe and a deliberately broken subject fails the relevant probe (mutation-strength proof).
5. Python-emitted canonical wire fixtures are consumed by the TypeScript contract test with ACP
   discriminators and content blocks unchanged. Thought fixtures contain no assistant-text duplicate.
6. Focused Python tests, `npm --prefix web run check`, `npm --prefix web test`, and the independent
   implementation review pass. The orchestrator runs `./verify` only after the reviewed slice settles.

## Review and handoff

A planning sub-agent writes `plan.md` without changing this contract. A different review sub-agent
checks the plan against this contract, owner brief, reviewed program, pins, donor source, and current
code. An implementation sub-agent then edits only the allowed files. A different review sub-agent
reviews the implementation diff and tests. Full reviews, dispositions, implementation report, and
focused-test evidence remain in this ticket directory.
