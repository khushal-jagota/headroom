# Panels TypeScript server foundation

This package is the shared server shell for the backend migration. It runs `.ts` files directly on Node 22.18+ and Node 24. Python still creates and upgrades its database. The migration branch remains separate from staging and main.

From the repository root:

```sh
scripts/dev npm --prefix server ci
scripts/dev npm --prefix server run check
scripts/dev npm --prefix server run test
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
