# Panels TypeScript server foundation

This package is the shared server shell for the backend migration. It runs `.ts` files directly on Node 22.18+ and Node 24. Python still creates and upgrades its database. The migration branch remains separate from staging and main.

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

Launch `node server/main.ts` from the repository root with isolated `PLAN_*` settings. The listener comes from `PLAN_SERVER_LISTENER_FD` when present. Otherwise the server binds loopback at `PLAN_PORT`. The database must exist. The process takes the same owner lock as Python before it opens the database. The dispatcher lock remains separate.

## How an area adds itself

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

An area that owns a commit projection exports a synchronous `CommitStep` from its data layer. Add that function to `commitSteps` in `server/areas.ts`. Keep the global Python order: signal publication, artifact links, navigation. `main.ts` supplies this list to the database before it accepts requests. The foundation leaves the list empty until those units exist.

A step receives the same transaction capability as a state transition. It must not commit, open another writer, retain SQL access, or return a promise. Any step failure rolls back the entire transaction. A successful transaction emits one contentless signal if rows changed. Use `{ changeSignal: 'silent' }` only for the existing Python cases that do not invalidate screens.

`createReadAccess(path)` supplies read-only connections for worker threads. It cannot create another writer. A future write-heavy rebuild needs exclusive writer transfer and all commit steps before it can run in a worker.

## Test selection and current boundaries

Set `PANELS_TEST_SERVER_BACKEND=typescript` to select this process in the existing server fixture. The fixture still creates the database through Python and passes its listener. Explicit Python test-host modules stay on Python. Unit 15 will connect its conversation host through this selection point.

This unit does not supply domain routes, schema migration, projection implementations, conversation hosts, background loops, or deployment supervision. Existing tests that require those pieces remain unavailable on the foundation alone.

## Known follow-ups

The write door prepares each statement on every call. Measure this cost at gate 3 before any statement cache is added.

Node 24 with npm 11 needs a development-guard change for npm's injected script environment keys. The current guarded alternatives above remain the launch route until that change lands. This unit does not change the guard policy.

## Comparison host

`node server/test_host.ts` starts the normal application with additional test-only route registrations. It requires `PLAN_TEST_MODE=1`. It uses the same database, locks, inherited listener, and shutdown path as `main.ts`.

Each area adds one registration to the `testAreas` list in `test_host.ts`. Test routes do not enter the normal `areas` list.

For a comparison list with `"conversation_system": "in_memory"`, the runner supplies `PLAN_COMPARE_CONVERSATION_SYSTEM=in_memory` to both hosts. The TS host creates one `InMemoryConversationSystem` for its test routes and all ordinary areas. Areas receive it as `services.conversationSystem`. The test endpoints start a conversation, send a message, read fake state, and complete a turn beneath `/api/test/in-memory-conversations`.

Without this switch, the host does not register those endpoints or inject an in-memory system. Unit 15 supplies the real conversation system. Units 7 through 10 can then run their lists without the switch against the real core.

## In-memory conversation instrument

`conversation/contracts.ts` is the 15-member in-process conversation boundary.
`conversation/in_memory_conversation_system.ts` mirrors the Python fake without database rows or agent output.
Backend adapters implement `conversation/backends/contracts.ts`. Their 19 event classes and 11 exception classes follow the current Python source.
Message content remains an ordered array of typed pieces. Runtime callers use `text_message_content` or the piece classes.

Until the real core exists, navigation can use `idle_publication(publish)` from `conversation/publication/idle.ts` as the first commit step.
It calls `publish(tx, conversation_id, attention)` for every stored conversation on that same transaction.
Each attention value has all four marks false. Navigation owns its storage, installation metadata, and registration in `commitSteps`.
The stand-in does not alter the fake's actual busy state and does not implement the real publication coordinator.

`IdlePublicationAuthority` in `conversation/publication/authority.ts` supplies runtime permits for the idle source.
Its constructor requires explicit `scope`, `runtime_epoch`, `installation_id`, and `delivery_session_id` values. Create it after readiness and runtime ownership are established. Its source permit keeps the Python fields and a numeric generation.
The navigation adapter returns the generated `CurrentAuthority` wire shape with decimal-string generations.
Call `invalidate('shutdown')` before shutdown and ownership release. Invalidation fences reads synchronously before it notifies subscribers.
Subscriptions are one-shot and can unsubscribe. One failed subscriber does not prevent the others from receiving invalidation.
A replacement installation creates a new instance. A persisted row never grants this runtime authority.

The shared lifecycle also supplies `services.signalAuthority`, an idle publication authority, after it owns the runtime and opens the database. All areas share that instance. Shutdown invalidates its permits and notifies its watchers before the application closes or runtime ownership ends. Unit 15 replaces the idle source with the real coordinator.

To compare the in-memory instrument against TypeScript, run:

```sh
scripts/dev pytest tests/compare/test_compare.py --compare-list tests/compare/lists/unit-06.json --compare-right-module tests.compare.ts_launcher
```

Lists for units 7 through 10 select both in-memory systems with `"conversation_system": "in_memory"`. Omit that setting when unit 15 supplies the real core. The comparison fixture passes the selection to both processes explicitly, rather than through ambient `PLAN_*` variables. The runner's default candidate remains Python for its reference calibration and existing CI job; `--compare-right-module tests.compare.ts_launcher` selects the TS comparison.

The shared `startServer` lifecycle accepts `createSignalAuthority(database)` for the real coordinator or an isolated test source. Its default factory, `createIdlePublicationAuthority`, reuses the persisted source scope and creates fresh runtime, installation, and delivery identities. The lifecycle invokes the factory only after runtime ownership and database access are ready. Factories return the structural public authority interface, so replacement coordinators do not inherit the idle class.
