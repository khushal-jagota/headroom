# Navigation source registration

Composition creates one runtime with `createNavigationRuntime(idle_publication)`. The write door calls its `publicationCommitStep` before its `navigationCommitStep`. Startup calls `runtime.initialize(database)` before requests arrive. This installs publication storage and rebuilds navigation through the same write door. Python owns the schema and source triggers.

A later domain exports a `NavigationSource` and appends it to `navigationSources` in `index.ts`. Supply:

- `roots(sql)` to enumerate source identities for startup rebuild.
- `affected(sql, dirty)` to resolve new or changed roots from a trigger entry.
- `derive(sql, id)` to return records and source dependencies, including an empty record list after deletion.
- `view(sql, request)` when the domain adds scoped windows, records, counts, or review context. Return the complete bounded record closure for its windows. Shared counts are additive, so do not count another source's records twice.

The framework also resolves prior dependencies. A failed derivation rolls back the source write through the existing write door. Sources receive transaction-scoped SQL and must not retain it or return promises.

Add the domain's records and their mutations to its comparison list, with a snapshot after each write and a restart before the final snapshot. This extends the rebuild proof. The first unit proves only Projects and the required baseline Chief record. The populated proof finishes at gate 3.

Projects have no navigation window membership in Python. A Project-only snapshot therefore does not expose stored Project records. Its route comparison cannot prove each Project record's derivation independently. The result must retain this limitation.

`createNavigationRuntime` accepts a `PublicationFactory` from `navigation/contracts.ts`. The conversation unit's `idle_publication` factory supplies complete idle attention facts through a synchronous callback. It supplies no permit or storage. Navigation owns the installation, per-conversation revisions, batch cursor, runtime epoch, and delivery session. New or changed facts advance the stored cursor. Deleted conversations retain revisioned tombstones. Unchanged idle facts do not rewrite rows.

`runtime.initialize(database)` captures the complete conversation set, commits a fresh installation, and then activates delivery authority. A failed transaction leaves delivery unavailable. The routes receive authority through `createRoutes(authority, sources)`, without shared global configuration. `capture` and `validate` check current runtime identity. `validateSnapshot` checks the installation identity inside the read transaction. `watch` revokes open streams and pending drains when `runtime.close()` ends authority. Composition must call `close` before it closes the app and database. Persisted records alone cannot grant live authority.

This adapter proves the idle source only. It does not infer live attention, owner association effects, or producer availability changes. The real conversation core replaces the idle source integration and supplies proof for those transitions. It must retain installation checks and immediate delivery revocation.

# Resource provider registration

Export a provider that implements `ResourceProvider` from `server/resource_versions/contracts.ts`. Its `capture(sql, request, key)` returns null for an unrelated key, or a revision and body reader. Add it to `providers` in `server/resource_versions/registry.ts`. Both run inside one read snapshot. Neither writes.

Each provider's domain comparison must prove its recognized keys, unchanged and changed revisions, coherent bodies, and relevant authority rules. This unit proves only the empty registry: unknown revisions are null and an unregistered body returns 404.
