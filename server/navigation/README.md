# Navigation source registration

Composition creates one runtime with `createNavigationRuntime(idle_publication)`. The write door calls its `publicationCommitStep` before its `navigationCommitStep`. Startup calls `runtime.initialize(database, signalAuthority)` before requests arrive. This installs publication storage and rebuilds navigation through the same write door. Python owns the schema and source triggers.

A later domain exports a `NavigationSource` and appends it to `navigationSources` in `index.ts`. Supply:

- `roots(sql)` to enumerate source identities for startup rebuild.
- `affected(sql, dirty)` to resolve new or changed roots from a trigger entry.
- `derive(sql, id)` to return records and source dependencies, including an empty record list after deletion.
- `view(sql, request)` when the domain adds scoped windows, records, counts, or review context. Return the complete bounded record closure for its windows. Shared counts are additive, so do not count another source's records twice.

The framework also resolves prior dependencies. A failed derivation rolls back the source write through the existing write door. Sources receive transaction-scoped SQL and must not retain it or return promises.

Add the domain's records and their mutations to its comparison list, with a snapshot after each write and a restart before the final snapshot. This extends the rebuild proof. The first unit proves only Projects and the required baseline Chief record. The populated proof finishes at gate 3.

Projects have no navigation window membership in Python. A Project-only snapshot therefore does not expose stored Project records. Its route comparison cannot prove each Project record's derivation independently. The result must retain this limitation.

`createNavigationRuntime` accepts a `PublicationFactory` from `navigation/contracts.ts`. The conversation unit's `idle_publication` factory supplies complete idle attention facts through a synchronous callback. Navigation owns installation storage, per-conversation revisions, and the stored batch cursor. New or changed facts advance that cursor. Deleted conversations retain revisioned tombstones. Unchanged idle facts do not rewrite rows.

The conversation dependency supplies producer authority through `AreaServices.signalAuthority`. Its public interface supplies `capture`, `validate`, and `watch`. Navigation declares the equivalent consumer interface in its own contracts. It does not create producer identities, availability, generations, delivery sessions, or permits.

`runtime.initialize(database, signalAuthority)` captures that producer's permit and commits an installation with the same scope, runtime epoch, and installation identity. It also rebuilds navigation. Failed startup does not admit requests. The routes receive the same dependency through area registration. A SQL installation check runs inside each navigation read transaction. The producer validates runtime authority before sends. Its watcher revokes open streams and pending drains on invalidation. Composition invalidates the producer before it closes navigation storage, the app, and the database. Persisted records alone cannot grant live authority.

This adapter proves the idle source only. It does not infer live attention, owner association effects, or producer availability changes. The real conversation core replaces the idle source integration and supplies proof for those transitions. It must retain installation checks and immediate delivery revocation.

# Resource provider registration

Export a provider that implements `ResourceProvider` from `server/resource_versions/contracts.ts`. Its `capture(sql, request, key)` returns null for an unrelated key, or a revision and body reader. Add it to `providers` in `server/resource_versions/registry.ts`. Both run inside one read snapshot. Neither writes.

Each provider's domain comparison must prove its recognized keys, unchanged and changed revisions, coherent bodies, and relevant authority rules. This unit proves only the empty registry: unknown revisions are null and an unregistered body returns 404.
