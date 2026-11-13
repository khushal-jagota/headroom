# Panels TypeScript server foundation

This package is the shared server shell for the backend migration. It runs `.ts` files directly on Node 22.18+ and Node 24. Python still creates and upgrades its database. The migration branch remains separate from staging and main.

`server/main.ts` selects the required production entry in normal mode. Until 4b supplies its real backend/environment composition, it fails clearly before startup. The existing test-mode switch preserves the bare fixture entry in that same file. It adds no provider, host, or flag. Packaging and publication cannot claim production composition from a test-mode entry.

Accepted migration differences live in [DEVIATIONS.md](DEVIATIONS.md).

`ios_compatibility/check.ts` hashes the accepted production TypeScript inventory, five fixed JSON inputs and two retained Hermes Python sources. It includes raw comments and uses Python Path component order. The checker and HTTP middleware share `ios_compatibility/policy.ts` for the native build floor. A human must supply `ios_compatibility/review.json`; no compatible verdict is copied from Python or written automatically. Missing fixed inputs, including the generated migration graph, fail the check. Publication integration and signed-device evidence remain separate requirements.

From the repository root:

```sh
scripts/dev npm --prefix server ci
scripts/dev npm --prefix server run check
scripts/dev npm --prefix server run test
```

TypeScript formatting is enforced by Prettier in CI, with a line width of 100 and single quotes. From the repository root:

```sh
scripts/dev npm --prefix server run format
scripts/dev npm --prefix server run format:check
```

The dependency guard checks the selected package before these commands. npm 11 adds a script environment key that the existing guard does not accept. For Node 24, use npm 10, or check the package before direct Node checks, as CI does:

```sh
scripts/dev check-node server
node server/node_modules/typescript/bin/tsc --noEmit -p server/tsconfig.json
cd server
node node_modules/vitest/vitest.mjs run
```

`node server/main.ts` in normal mode requires the complete real production dependencies. Test fixtures use its existing test-mode path with isolated `PLAN_*` settings. Once admitted, the listener comes from `PLAN_SERVER_LISTENER_FD` when present, otherwise loopback `PLAN_PORT`. The database must exist. Startup takes the same owner lock as Python before it opens that database. The dispatcher lock remains separate.

Startup installs signal handlers before asynchronous setup. Before a writer opens, readonly admission compares the complete existing Alembic revision set to `core/database/application_schema.json::alembicRevision`. Missing, empty, older, newer, unknown, and extra revisions fail with actual and expected values. Startup never migrates or stamps that database. Fresh test fixtures seed their own revision metadata explicitly.

Area preparation retains the exact new Ticket integrity faults from one audit. The owned recovery flight sends their ordinary owner-to-Chief system notices before lost-background-task notices and automatic recovery. A failed notice logs and does not stop startup. App and environment shutdown record each actual identity before callbacks, so a late setup continuation never closes the same identity again. A stop after successful listen is normal shutdown. A pre-listen failure remains a startup error.

`PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS` enables the existing opt-in identity refusal;
it defaults to false and cannot be enabled by YAML. The application consumes the
variable when loading its actual process environment, before agents inherit it.
A supervisor must load configuration from an environment copy and preserve the flag
for its application child. Unidentified loopback callers need same-user socket-holder
evidence outside the exact agent cgroups. Trusted owners, identified actors and
non-loopback callers keep the source bypass rules. The accepted TypeScript service
name and rollback prerequisite are recorded in [DEVIATIONS.md](DEVIATIONS.md).

The default peer resolver refuses before constructing its proc context or doing I/O
when `VITEST=true`, `PYTEST_CURRENT_TEST` is present, or `PLAN_TEST_MODE` is a truthy
test-mode value. Tests can supply an explicit private `PeerLookupContext` to exercise
the resolver. The current raw WebSocket preview upgrade requires Tailscale-owner
ingress. There is no general application WebSocket route: the exported close-1008
decision is source-compared, with transport wiring reserved for Unit 22.

One direct comparison reuses Python's synthetic proc fixtures and Fastify injection, without a listener or database.
It covers peer evidence, bypasses, refusal values, config consumption and the default test I/O guard. Its body takes 0.51 seconds, with zero skill-tree guard receipts.
TypeScript, Ruff and mypy pass. Added helpers total 423 lines against 426 product lines (99.3%).

## How an area adds itself

Navigation `createRoutes()` returns `{routes,start}`. Previews use `createPreviewRoutes()` instead of a global routes export. `areas.initialize()` prepares the record without starting replay or stale-epoch cleanup. Explicit route services and replay start after application readiness. Production composition starts one idle-child janitor in normal mode, even without dispatcher admission. Test mode skips that janitor, unlike Python. The explicit checker can run in test mode, but only writes findings/heartbeat and cannot notify.

Environment composition preserves the actual executor object's members. It does not spread away prototype methods. A genuine `executor:null` remains absence. Remote retained children with `stop_outside_lock:true` remain unsupported: delivery throws `BackendSpawnFailed('Backend did not retain its run identity')` before local child publication. Executor integration must resolve that existing boundary explicitly. These contracts do not authorize a production provider, release, or service operation in tests.

Create `server/<area>/contracts.ts`, `logic/`, `data/`, and `routes.ts` as needed. Keep pure rules in `logic/`. Import the existing generated frontend contracts with `import type` until cutover.

Export a Fastify plugin from `routes.ts`:

```ts
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';

export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get('/example', async () => services.database.read(sql =>
    sql.all<{ id: string }>('SELECT id FROM example ORDER BY id')));
  app.post('/example', async request => {
    const caller = services.identity(request).principal;
    return services.database.write(tx => {
      // Validate this area's input before SQL. Use the existing Python error contract.
      tx.run('INSERT INTO example (id) VALUES (?)', [caller.id]);
      return { ok: true };
    });
  });
};
```

Add its import and one entry to `areas` in `server/areas.ts`. The shared registration helper supplies configuration, clock, identity, read access, and the write door. It applies authority admission before each handler. Preserve Python's route parameter names. Measure areas supply the admission dependencies declared in `core/authority/intake.ts` when they register.

An area that owns a commit projection exports a synchronous `CommitStep` from its data layer. Add that function to `commitSteps` in `server/areas.ts`. Keep the global Python order: signal publication, artifact links, navigation. `startServer` supplies the real publication, artifact-link, and navigation steps to the database before it accepts requests.

A step receives the same transaction capability as a state transition. It must not commit, open another writer, retain SQL access, or return a promise. Any step failure rolls back the entire transaction. A successful transaction emits one contentless signal if rows changed. Use `{ changeSignal: 'silent' }` only for the existing Python cases that do not invalidate screens.

`createReadAccess(path)` supplies read-only connections for worker threads. It cannot create another writer. A future write-heavy rebuild needs exclusive writer transfer and all commit steps before it can run in a worker.

## Test selection and current boundaries

Set `PANELS_TEST_SERVER_BACKEND=typescript` to select TypeScript in the existing server fixture. The fixture still creates the database through Python, supplies synthetic homes, and passes its bound listener. The two explicit modules `tests.e2e.rail_server` and `tests.e2e.conversation_signals_server` select `server/test_host.ts`; other explicit test-host modules stay on Python. With no explicit module, the selector uses `server/main.ts`.

Registered areas supply their domain routes and projections. The conversation test host supplies a scripted backend through the real core. Python still owns schema creation/migration. Real production binding and packaged supervision remain later gates. Embedded `startServer` has unavailable catalog/sign-in providers by default, while an explicit normal-mode embedding still permits generic machine probes. It is not a claim that all machine I/O is disabled.

## Known follow-ups

The write door prepares each statement on every call. Measure this cost at gate 3 before any statement cache is added.

Node 24 with npm 11 needs a development-guard change for npm's injected script environment keys. The current guarded alternatives above remain the launch route until that change lands. This unit does not change the guard policy.

## Comparison host

`node server/test_host.ts` starts the normal application with additional test-only route registrations. It requires `PLAN_TEST_MODE=1`. It uses the same database, locks, inherited listener, and shutdown path as `main.ts`.

Each area adds one registration to the `testAreas` list in `test_host.ts`. Test routes do not enter the normal `areas` list.

For a comparison list with `"conversation_system": "in_memory"`, the runner supplies `PLAN_COMPARE_CONVERSATION_SYSTEM=in_memory` to both hosts. The TS host creates one `InMemoryConversationSystem` for its test routes and all ordinary areas. Areas receive it as `services.conversationSystem`. The test endpoints start a conversation, send a message, read fake state, and complete a turn beneath `/api/test/in-memory-conversations`.

Without this switch, the host composes `SqliteProcessConversationSystem` with the record runtime's single store and tail. `createConversationCore(context)` receives that runtime and the shared clock after record initialization. Runtime facts attach to that same record runtime. Shutdown begins HTTP closure and core cancellation together. Actual request and core work must settle before the record and database close.

The comparison host uses `conversation/testing/scripted_process.ts` behind both Python and TypeScript backend adapters. Existing signal controls retain their meanings. Configuration and inspection controls expose process behavior and actual backend observations; backend news enters through the real core rather than direct event-row inserts.

The TS host also supplies ten existing browser controls: signal `start`, `finish`, `message`, `queue`, `questions`, `answers`, `reject-next-prompt`, and `delivered`, plus connected `bind` and `message`. Optional Ticket starts preserve the synthetic Worker role and canonical association. Answers come from the latest child; delivered content retains the history across child replacement. The backend snapshot unit supplies the eleventh `ticket-model-catalog` control through its snapshot-service injection seam and owns the synthetic catalog and credential-state fixture.

The four core `unit-15.*` lists compare all 21 conformance scenarios through start, state, send, interrupt, kill, and permission-answer routes. They compare the conversation row, ordered events, lifecycle facts, all five view tables, runtime state, and process observations. The complete product route surface and existing browser-fixture selection are present below. New paired route comparisons and earlier-unit rerun evidence remain separate gates; route presence does not claim those results.

## Conversation HTTP surface

`conversation/routes.ts` supplies sixteen route seams under `/api/conversation` when a real core is composed:

| Method | Path |
|---|---|
| POST | `/conversations` |
| GET | `/conversations/:conversation_id` |
| POST | `/conversations/:conversation_id/owner-read` |
| GET | `/conversations/:conversation_id/composer-catalog` |
| GET | `/conversations/:conversation_id/events` |
| GET | `/conversations/:conversation_id/events/:sequence/detail` |
| GET | `/conversations/:conversation_id/tail` |
| POST | `/conversations/:conversation_id/send` |
| GET, HEAD | `/conversations/:conversation_id/files/:stored_file_id` |
| POST | `/voice-transcriptions` |
| POST | `/conversations/:conversation_id/interrupt` |
| POST | `/conversations/:conversation_id/kill` |
| DELETE | `/conversations/:conversation_id/held-prompts/:held_prompt_id` |
| POST | `/conversations/:conversation_id/held-prompts/:held_prompt_id/promote` |
| POST | `/conversations/:conversation_id/permission-answers` |
| POST | `/conversations/:conversation_id/user-input-answers` |

The six record-owned `/view`, `/view/pages`, `/view/body`, `/view/tail`, `/view/receipts`, and `/view/body/content` routes remain on that same record runtime, store, and live tail. Startup reconciles once and rotates one epoch. Product routes do not create another store or rerun reconciliation.

The event/live tail subscribes before replay, joins at its sequence high-water mark, and emits separate `conversation-event` and `conversation-frame` SSE names. It includes the initial held-line refresh and heartbeat comments, and closes its subscription on disconnect. The selected-view tail remains the record owner's protocol. Event paging caps a page at 100 rows; public tool detail is capped at 1024 Unicode code points, with full readable output available through the detail route.

Owner-read and both answer routes require the owner. Mutations preserve the active Ticket conversation guard, and raw sends reject employee-owned conversations. All five send entry points share the existing 20 MiB pre-parser limit, including streamed bodies. Kept message-file reads reuse the file delivery implementation, with private immutable caching and `nosniff`.

`ConversationHttpWriteScope` owns owner-read writes through SQLite BUSY contention without adopting core runtime state. Shutdown seals this scope before new writes can enter. Its actual drain and core cancellation begin before the shared wait. Both must settle before record cleanup. Other semantic errors propagate once. It does not change the record contract or retry provider actions.

Voice transcription currently provides request-shape validation and the exact unconfigured 503 answer. No configured transcription provider is connected; audio/media/base64 processing and provider policy remain with the uploads/service unit. Valid video upload and credential-wire gates also remain with their owners. Backend catalog/admin/sign-in services are outside the sixteen seams above. A stored composer catalog read is separate from backend discovery.

The scripted test host can open and continue conversations through these routes. Normal `main.ts` still has no provider factory: a successful scripted browser session does not claim that installed-provider delivery is available.

## Claude backend child

`create_claude_backend_child_factory` in `conversation/backends/claude_agent_sdk/adapter.ts` takes an explicit `claude_executable` and optional `environment_overrides`. Conversation identity overrides the launch environment. The adapter uses SDK `0.3.316`. It checks the selected executable against the `2.1.219` minimum before startup. It does not select the SDK's bundled CLI. Production provider composition remains outside this unit.

The SDK public spawn hook owns the exact child and its required WorkerUnit. An input queue offer proves neither a completed write nor native acceptance. Ordinary delivery needs UUID admission receipts; native commands retain Python's write-completion contract. Stop requires a valid cancellation receipt with an empty `still_queued` set and settlement of the active command.

`tests/compare/direct/test_unit18_adapter.py` drives the TS scripted CLI through both real adapters and conversation cores. It compares events, lifecycle facts, view rows, and delivery fates. This scripted proof does not establish real-provider coverage of all 19 backend events.

On Linux, `BASH_ENV` points to the absolute source path of `server/conversation/tool_shell_startup.bash` for TS and `src/planner/conversation/tool_shell_startup.bash` for Python. The direct proof permits only this exact source-location pair and requires identical file bytes. A byte difference fails the proof. Other environment differences require explicit acceptance in `DEVIATIONS.md`.

The paired scripted proof also exercises the real CLI's observed ordering, status and rate-limit frames, and optional message fields. The one-attempt provider driver and its results remain Ticket artifacts, outside automatic discovery.

## In-memory conversation instrument

`conversation/contracts.ts` is the 15-member in-process conversation boundary.
`conversation/in_memory_conversation_system.ts` mirrors the Python fake without database rows or agent output.
Backend adapters implement `conversation/backends/contracts.ts`. Their 19 event classes and 11 exception classes follow the current Python source.
Message content remains an ordered array of typed pieces. Runtime callers use `text_message_content` or the piece classes.

The shared lifecycle supplies the real `SignalPublicationCoordinator` as `services.signalAuthority`.
Each server creates one record runtime and reconciles once under its existing ownership handle.
The core reuses that store and tail. Publication binds its live-turn reader before initialization and navigation attachment.
The source transaction runs publication, its navigation participant, artifact links, and navigation finalization before commit.
The core's save-attempt scope rolls runtime field mutations and producer dirty marks back with the record.
Only committed rows reach the tail. The five status readers also check the current Signal authority.

The record participants also use the Manager wake area's canonical Ticket failure target and durable notice-batch membership readers. `create_ticket_failure_notice` and `all_sender_messages_are_manager_notices` live in `manager_wakes/data.ts`; the existing `create_turn_failure_wake` remains the only failure-wake writer. Admission uses that area's existing `admit_notice_batch` and preserves its notice flag and suppression reason. These bindings do not implement deployment receipt settlement or queued-notice marking.

The `createConversationCore(context)` factory receives the shared publication coordinator.
Core adapters pass `context.publication.save_attempt`, a `current_read` callback, and `context.revoke_signal` through the corresponding core options.
Core construction requires both `model_is_enabled` and `execution_refusal`. Bind the first to `conversation/backend_state.ts::model_is_enabled` through the actual owned `context.database`. Bind the second to `tickets/execution_admission.ts::conversation_execution_refusal` with an explicit policy. Shared-core tests use `canonical_ticket_execution_refusal`. Temporary production composition uses the local-only policy until the real executor router lands. Neither callback defaults to permission, and neither reads a closed fixture database.
Core factories must also bind `on_held_model_change` to `write_held_ticket_configuration` from `runtime/conversation_start.ts`, with the same database and clock. The test host supplies this binding. After an accepted held model/effort change, it calls the existing guarded Ticket writer for the active conversation; it does not change the backend or repeat the prompt. A secondary Ticket-write error propagates after the accepted receipt settles. Future provider factories need this same binding.
Without a core factory, the server supplies record reads and real publication from durable facts, with no local turn or substitute provider.
An explicitly selected in-memory instrument remains separate. The idle authority helpers remain for their isolated tests.

Shutdown seals new request/write admission and stops new connections before any wait.
It closes event streams immediately. Ordinary admitted responses and media work can finish during the shared grace.
At grace expiry it logs named pending stages, closes remaining connections, and aborts remaining media work.
Handlers, hooks, iterator operations, provider work, and native resources remain owned after a connection closes.
Worker readiness and Manager wakes cancel at stop start. Every independent stop begins before drain waits.

All service observers require the same `StopOptions` absolute monotonic deadline. No leaf creates a fallback budget. Their required `settled()` joins report actual work and cleanup. Embedded close callers supply their initial options. Comparison close hooks read the already-published options through `TestCompositionContext.shutdown_options()`.
The application keeps waiting after grace. When every actual stage and native resource settles, it runs ordered finalizers automatically.
Settled failures do not prevent safe finalization. Actual entries log active resource types and deliberately exit 0 after clean completion or 1 after collected failures. Embedded `startServer` receives no exit capability unless its caller supplies one.
There is no manual finish operation and no renewed budget on repeated signals.
A rejected detach cannot prove a child stopped: the invocation also owns actual process close and Worker-unit confirmation.

The canonical values in `lifecycle/configuration.ts` are **30 seconds grace, 45 seconds hard-phase trigger, and 60 seconds supervisor timeout**.
At the hard phase, the application logs named pending stages, kills still-owned unreaped groups, and attempts one batch of exact owned Worker units.
That control attempt has at most the existing 10-second budget and original stop +55 seconds. It logs an unconfirmed force failure honestly.
Then the application exits non-zero without database/record/lock finalizers under pending work.
The 45-second value starts forced termination; it does not claim unconditional process exit at exactly 45 seconds.
Node timers cannot interrupt synchronous JavaScript or SQLite. The separate supervisor fallback is required for a blocked application loop.
New TypeScript packaging must consume the exported 60-second supervisor APP fallback. Packaged policy uses an 80-second stop client and a 90-second outer unit phase. The legacy Python rehearsal's 30-second unit is not its rehearsal route. Those packaged consumers remain unmerged and unproved by this application change.
Real new-unit/supervisor timeout proofs remain part of the later packaging work.

Registry mutation and direct startup share a stable database-derived authority outside the instance tree.
Reset/remove hold it through the complete swap, walk, and manifest publication. They also retain the legacy supervisor and DB locks.
Inspection observes existing kernel lock evidence without creating or acquiring locks. It is a point-in-time report, not mutation permission.
Normal Python supervisor launches remain excluded through the existing port lease.
An unmodified legacy Python direct launch does not take the new stable guard after its old DB sidecar moves; that mixed-version race is not claimed solved.
The service/interface hand-over explains the shared `child_processes` and `one_shot_children` ownership arguments.

To compare the in-memory instrument against TypeScript, run:

```sh
scripts/dev pytest tests/compare/test_compare.py --compare-list tests/compare/lists/unit-06.json --compare-right-module tests.compare.ts_launcher
```

Lists for units 7 through 10 select both in-memory systems with `"conversation_system": "in_memory"`. Omit that setting when unit 15 supplies the real core. The comparison fixture passes the selection to both processes explicitly, rather than through ambient `PLAN_*` variables. The runner's default candidate remains Python for its reference calibration and existing CI job; `--compare-right-module tests.compare.ts_launcher` selects the TS comparison.

The optional `beforePublication(database)` hook performs test-host projection bootstrap after record reconciliation and before producer initialization.
It does not replace the producer authority. `testAreas` receives the shared core, record, publication, clock, and database.
`startServer` returns its listener address and an idempotent close operation for isolated lifecycle tests.

Vitest removes all `PLAN_*`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `HERMES_HOME` variables before test files load.
Each test file starts with a fresh temporary `HOME`, which tests can override explicitly.
Vitest and `PLAN_TEST_MODE=1` hosts can only open databases inside the OS temp folder or an explicitly registered test root.

Use `requestOrigin(request)` from `core/http/origin.ts` for public URL construction.
It matches Uvicorn's default trusted local peer (`127.0.0.1`) and validated
`X-Forwarded-Proto` schemes. It keeps the actual Host and does not trust
`X-Forwarded-Host`. Nondefault `FORWARDED_ALLOW_IPS` configuration is not part of
this helper's current contract.

`panels` CLI calls keep the Worker's own `PLAN_ACTOR`, `PLAN_TICKET_ID`, `PLAN_MANAGER_ID`, and `PLAN_CONVERSATION_ID` values; only repository code runs with inherited `PLAN_*` removed.

## Codex app-server transport

`conversation/backends/codex_app_server/` supplies the small consumed wire contracts,
JSONL client, saved-login reader, inherited child environment, and bounded process-group
ownership. `adapter.ts` exports `create_codex_backend_child_factory` for the real core.
`providers.ts` supplies the fixed model, saved-login, sign-in, and usage bindings.
Production snapshot composition receives its database and clock from `core/start.ts`.
The sign-in service and snapshot reads share the durable login-rejection state.
The lifecycle composition must supply `remembered_login_rejection` to the Codex child
factory. Use `create_durable_codex_login_rejection(database, now)` from `backend_state.ts`,
with the same database and clock as the cards and sign-in. No factory default substitutes
in-memory rejection state. Sign-in resolves its executable from the current Codex snapshot.

The client checks the selected CLI version before it starts the app-server. Unknown
notifications and item kinds remain unconsumed. A malformed consumed notification warns
and drops. Session, turn, outcome, permission, and numeric-kind checks remain strict.
Raw response text is retained for usage reads that must preserve numeric token spelling.

The direct Unit 17 checks compare fresh Python answers and the same scripted TypeScript
executable. The startup check intercepts all provider and service-manager launches and
records complete environments, command arguments, and working directories.
On Linux, the one child-environment difference is `BASH_ENV`: Python names
`src/planner/conversation/tool_shell_startup.bash`, and TypeScript names
`server/conversation/tool_shell_startup.bash` through the shared TypeScript helper.
The Manager requested this runtime independence from Python source. The startup check
asserts both exact paths, compares the scripts byte for byte, and compares every other
environment variable, command argument, and working directory without normalization.

### Codex process lifetime and remaining wire differences

TypeScript starts Codex in a detached process group and a new session. Normal cleanup
signals that owned group while its direct child remains alive. After it observes the
child's exit, it does not signal the numeric group id, which the OS can reuse. WorkerUnit
cleanup retains the existing service-manager containment for Worker conversations.

If Node dies before cleanup, the OS closes its stdin pipe. Codex exits when it reads EOF.
There is no parent-death signal or server-supervisor replacement in this adapter. Python's
uncontained child shares the server's session instead of starting a new one. Descendants
that survive a natural leader exit outside WorkerUnit are not cleaned up through a late
group signal.

The Manager requested documentation of these remaining wire differences:

- JSON integers above `2^53` can lose precision when projected into JavaScript numbers.
- Python and JavaScript disagree on blank identifiers containing U+0085 or U+FEFF.
  Python treats U+0085 as whitespace and U+FEFF as non-whitespace. JavaScript does the reverse.
- Python treats `id: true` as integer reply id 1. TypeScript does not correlate that reply.
- TypeScript replaces invalid UTF-8 bytes during stream decode. Python warns when the
  input cannot decode.
- If an application handler throws, TypeScript warns and continues its dispatch chain.
  Python's reader task ends.

These limits do not change the strict checks for consumed identities, outcomes, and
permission decisions. They are not hidden by broad comparison rules.

The Codex child preserves correlated admission, provisional-event buffering, native thread
resume, steer certainty, parked asks, catalog refresh, and generation-scoped login diagnosis.
Its paired direct proof uses the same scripted app-server behind both real SQLite cores.
Sixteen checkpoints compare ordered native requests, delivery fates, events, lifecycle facts,
all five populated view tables, live output, and durable login rejection. The script supports
numeric ask IDs, delayed JSONL fragments inside a UTF-8 character, and stdin EOF exit.
The driver waits for each child's native metadata request before its first prompt, so
catalog discovery precedes input without a scripted turn-response barrier. The pair
does not prove nonblocking discovery. It includes early turn-start news, refused steer,
delayed interruption, file and MCP items, retryable and final errors, and child exit
before and after admission. Request timeout and write-failure transport checks use
injected clients rather than this paired core run.

The real Codex 0.167.2 proof completed a fresh TypeScript turn and an exact recall
through TypeScript on a Python-created thread. Its manual driver, complete replies,
request evidence, and restore instructions remain Ticket artifacts outside source control.
The existing pair also covers native bookkeeping notifications between replies, both
turn-start orderings, historical usage on resume, and multiple agent-text fragments.
Unknown notification payloads stay synthetic where only native method names were retained.
Real sign-in timing, approvals, tools, and interruption remain side-by-side gate checks.

The adapter bounds ask withdrawal before child shutdown, then always invokes client cleanup.
The existing Python withdrawal wait is unbounded. The Manager accepted the two-second
bound in the Part 2 review. The difference is recorded in `DEVIATIONS.md`.

The provider proof compares paginated and partial model catalogs, login generations and
rejections, correlated sign-in completion and cancellation races, deadlines, shutdown,
and usage responses against fresh Python results. Usage returns the original JSON result
text and joins its child before completion. Sign-in preserves microseconds in its public
timestamps. Tests keep production probes unavailable in test context, including a context
change after provider construction. These scripted checks do not establish real login or
provider entitlement.
Provider shutdown refuses new sign-in and probe work, aborts active catalog and usage
reads, and joins them before the database closes. Both snapshot environment and sign-in
cleanup run even if either fails. Exact-second sign-in timestamps omit the fractional
part, as Python does. Nonzero microseconds retain six digits.

## Hermes ACP transport

`conversation/backends/hermes_acp/` supplies the Hermes backend:
the backend child, launch values, ordered ACP transport, and process ownership.
`create_hermes_backend_child_factory` is exported from `adapter.ts`.
`HermesModelCatalogOwner` and `hermes_model_catalog_launch` in `catalog.ts` supply
the retained inventory script binding. The lifecycle owner must retain the catalog
owner and call `shutdown()` when the server stops. The real 4b binding must retain
that raw shutdown promise beside the HTTP runtime, observe it with the shared
deadline, and include it in the backend `settled()` barrier. No owner is constructed
by today's missing production table. Production selection remains separate work.
Hermes retains Python's exact child environment,
including the absence of an automatic `BASH_ENV` override.

The adapter pins `@agentclientprotocol/sdk` to `2.6.0` and uses its ACP v1
`/unstable` entrypoint to retain prompt usage fields. The public `ClientApp` dispatches
handlers concurrently, so the adapter uses the SDK's lower-level `Connection` and
generated validators behind an admission gate. Notifications and request admission
finish before the next frame reaches the SDK. Prompt-ending and session-binding
effects also finish before a later frame proceeds. Raw SDK responses stay independent
from that effect barrier. Permission answers remain outside that gate. Notification admission has no timeout; the permission response-write
confirmation retains Python's 15-second bound. Close and wire failure release the gate.

The package exports no subpath for `Connection` or the generated validators.
Runtime imports resolve their files from the pinned package entrypoint. Relative
`node_modules` imports supply those same official types without a duplicate protocol
interface. Pin checks cover the consumed internal symbols. Any SDK upgrade requires
a new review of `conversation/backends/hermes_acp/client.ts` before approval.

`executor/vps_presence.ts` supplies the lease cache and work-route incarnation fence.
Its constructor receives the invocation database, mailbox, and change signal.
It owns its contact flush, presence timer, drain waits, and started callbacks.
Composition supplies the update, replacement, and returned callbacks before requests start.
Stop seals new work and observes the shared absolute deadline. `settled()` joins actual work and cleanup.
The service does not close its supplied database or mailbox.
