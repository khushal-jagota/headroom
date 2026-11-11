# Navigation source registration

The write door calls `navigationCommitStep` after the other projection steps. Startup calls `rebuildNavigation` through that same door before requests arrive. Python owns the schema and source triggers.

A later domain exports a `NavigationSource` and appends it to `navigationSources` in `index.ts`. Supply:

- `roots(sql)` to enumerate source identities for startup rebuild.
- `affected(sql, dirty)` to resolve new or changed roots from a trigger entry.
- `derive(sql, id)` to return records and source dependencies, including an empty record list after deletion.
- `view(sql, request)` when the domain adds scoped windows, records, counts, or review context. Return the complete bounded record closure for its windows. Shared counts are additive, so do not count another source's records twice.

The framework also resolves prior dependencies. A failed derivation rolls back the source write through the existing write door. Sources receive transaction-scoped SQL and must not retain it or return promises.

Add the domain's records and their mutations to its comparison list, with a snapshot after each write and a restart before the final snapshot. This extends the rebuild proof. The first unit proves only Projects and the required baseline Chief record. The populated proof finishes at gate 3.

Projects have no navigation window membership in Python. A Project-only snapshot therefore does not expose stored Project records. Its route comparison cannot prove each Project record's derivation independently. The result must retain this limitation.

The conversation contract supplies `NavigationAuthority.capture`, `validate`, and `watch`. Watch revokes open streams and pending drains. Until composition binds that adapter with `configureNavigationAuthority`, snapshot and event routes return 503. Persisted records cannot grant live authority.

# Resource provider registration

Export a provider that implements `ResourceProvider` from `server/resource_versions/contracts.ts`. Its `capture(sql, request, key)` returns null for an unrelated key, or a revision and body reader. Add it to `providers` in `server/resource_versions/registry.ts`. Both run inside one read snapshot. Neither writes.

Each provider's domain comparison must prove its recognized keys, unchanged and changed revisions, coherent bodies, and relevant authority rules. This unit proves only the empty registry: unknown revisions are null and an unregistered body returns 404.
