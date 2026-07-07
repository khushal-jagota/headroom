# Spike 08 — Reactive frontend: the option set

This problem has two separate axes:

1. **Propagation:** how the client learns what changed.
2. **Rendering:** how the client updates only the UI that depends on that change.

A transport like WebSocket, SSE, WebTransport, MQTT, or HTTP streaming is not itself the architecture. It only carries one of the payload styles below.

No ranking is implied.

## Server → Client Propagation

### 1. Reactive Query Engines / Live Queries

**What it is:**  
The client subscribes to a query. The backend tracks which data that query read, or maintains an incremental dataflow for the query result. When writes happen, the system determines which subscribed queries are affected and pushes new results or result deltas.

**How only changes change:**  
Only queries whose read-set/result-set changed are notified. Depending on the system, the client may receive a new query result, row-level additions/removals, or incremental deltas.

**Where changed-knowledge lives:**  
**Derived automatically** by the query engine, dependency tracker, index matcher, or differential dataflow system.

**Exemplars:**  
Convex, Firebase/Firestore listeners, RethinkDB changefeeds, Meteor live queries, Materialize `TAIL` / differential dataflow.

**Essential trade-off:**  
You get automatic dependency tracking by accepting the system’s query model, runtime, consistency rules, and operational constraints.

---

### 2. Row-Level / Database Change Subscriptions

**What it is:**  
The client, or an intermediary server, subscribes to database-level changes: inserted rows, updated rows, deleted rows, table notifications, WAL/logical replication records, or trigger-emitted messages.

**How only changes change:**  
The propagation layer sends the changed row/table record instead of forcing the client to reload everything. The client then updates or invalidates the affected local state.

**Where changed-knowledge lives:**  
**Derived automatically** at the database row/log level, but **hand-maintained** when mapping row changes to UI resources, aggregates, joins, or domain concepts.

**Exemplars:**  
Supabase Realtime, Postgres logical replication, Postgres `LISTEN/NOTIFY`, Debezium, MongoDB change streams.

**Essential trade-off:**  
Precise at the storage layer, but storage-level changes are often not the same as product-level changes. Joins, aggregates, permissions, and UI relevance still need mapping.

---

### 3. Domain Event Streams / Pub-Sub

**What it is:**  
The application emits typed semantic events after writes: `ticket_updated`, `sprint_closed`, `comment_added`, `status_changed`, and so on. Clients receive those events over WebSocket, SSE, pub-sub, or another push channel.

**How only changes change:**  
Each event carries a kind, entity id, payload, cursor, version, or affected ids. The client uses that to update or invalidate only matching resources.

**Where changed-knowledge lives:**  
Mostly **hand-maintained** in event schemas, event publishers, and client/server event-to-resource mappings.

**Exemplars:**  
Phoenix Channels, Rails ActionCable, Socket.IO event streams, custom typed WebSocket feeds, domain-shaped GraphQL subscriptions.

**Essential trade-off:**  
Clear semantic events, but correctness depends on every write path emitting the right events and every consumer mapping them correctly.

---

### 4. Delta / Patch Streaming

**What it is:**  
The server sends operations against a known client state: replace this JSON path, insert this list item, remove this field, apply this text operation, apply this CRDT update.

**How only changes change:**  
The client applies the patch directly to its local state or document. Only the patched paths/entities become dirty.

**Where changed-knowledge lives:**  
Can be **derived automatically** by a diff engine, OT engine, CRDT engine, or Immer-style patch generator. Can also be **hand-maintained** if the app constructs patches explicitly.

**Exemplars:**  
JSON Patch over SSE/WebSocket, JSON Merge Patch, Immer patches, ShareDB operational transforms, Yjs updates, Automerge changes.

**Essential trade-off:**  
Very granular, but requires careful versioning, ordering, idempotency, base-state agreement, and patch compatibility.

---

### 5. Normalized Client Caches + Entity Subscriptions

**What it is:**  
The client stores data in a normalized graph keyed by entity identity, usually type plus id. Queries/fragments subscribe to records or fields. Server pushes subscription payloads, mutation results, or live query results into that normalized cache.

**How only changes change:**  
An update to `Ticket:123.status` notifies only query results/fragments/components that read `Ticket:123.status`.

**Where changed-knowledge lives:**  
**Mixed.** Entity identity, field dependency, and fragment subscriptions are often **derived** by the cache. Subscription payload design, cache write policies, and aggregate updates are often **hand-maintained**.

**Exemplars:**  
Apollo Client, Relay Store, urql Graphcache, GraphQL subscriptions, GraphQL live queries.

**Essential trade-off:**  
Strong entity-level precision, but requires schema discipline, stable identities, cache policies, and special handling for lists/aggregates.

---

### 6. Keyed Resource Cache + Targeted Invalidation / Refetch

**What it is:**  
The app names resources with stable keys: `ticket:123`, `board`, `sprint:current`, `chat-status:abc`. Server events invalidate specific keys. Active subscribers refetch only those resources.

**How only changes change:**  
Only the invalidated keys refetch. Components subscribed to other keys do nothing. Inside an invalidated resource, the whole resource may still be refetched.

**Where changed-knowledge lives:**  
Mostly **hand-maintained** in the resource key design and event-to-key mapper. Some libraries **derive** structural sharing and avoid notifying subscribers if refetched data is equal.

**Exemplars:**  
TanStack Query / React Query, SWR, RTK Query, custom resource caches.

**Essential trade-off:**  
Simple and explicit, but precision is only as good as the key granularity and invalidation map.

---

### 7. Local-First / Sync-Engine Replicas

**What it is:**  
The client keeps a local replica: rows, documents, or CRDT state. A sync protocol exchanges deltas, revisions, operations, or changed rows between client and server. UI reads from the local replica.

**How only changes change:**  
Incoming deltas mutate only the affected local records/documents. Observable local queries then notify only affected subscribers.

**Where changed-knowledge lives:**  
Mostly **derived automatically** by the sync engine, replication protocol, revision system, or CRDT. Sync scopes, permissions, mutation semantics, and conflict policy are often **hand-maintained**.

**Exemplars:**  
Replicache, ElectricSQL, PowerSync, RxDB, PouchDB/CouchDB, WatermelonDB, Yjs, Automerge.

**Essential trade-off:**  
Strong local responsiveness and delta sync, at the cost of replica lifecycle, conflict handling, schema migration, storage, and sync authorization complexity.

---

### 8. Event-Sourcing / Log-Tailing Into Local Projections

**What it is:**  
The backend exposes an append-only event log. The client tails from a cursor and folds events into local read models using reducers/projections.

**How only changes change:**  
Each event is reduced into specific local state changes. Components subscribed to those local projection slices update.

**Where changed-knowledge lives:**  
Mostly **hand-maintained** in event definitions and projection reducers. Ordering, cursoring, and replay are provided by the log.

**Exemplars:**  
EventStoreDB-backed subscriptions, Kafka topic tailing, CQRS read-model streams, custom append-only domain event feeds.

**Essential trade-off:**  
Replayable and auditable, but reducers, snapshots, migrations, duplicate handling, and projection evolution become part of the app contract.

---

### 9. Server-Driven UI Fragment / DOM Patch Streams

**What it is:**  
The server sends UI operations rather than data operations: replace this fragment, append this row, remove this node, morph this component subtree, or apply a server-rendered DOM diff.

**How only changes change:**  
The server targets a DOM region or sends a computed DOM patch. The browser updates that region instead of rerendering the whole page.

**Where changed-knowledge lives:**  
**Mixed.** In Turbo/htmx-style systems, stream targets/actions are often **hand-maintained**. In LiveView/Livewire-style systems, DOM diffs are more **derived automatically** from server-side render state.

**Exemplars:**  
Hotwire Turbo Streams, htmx SSE/WebSocket extensions, Phoenix LiveView, Laravel Livewire.

**Essential trade-off:**  
The server can own very precise UI updates, but the UI lifecycle becomes coupled to server rendering and server-side component state.

---

### 10. Shared Real-Time State / Actor Stores

**What it is:**  
Clients join a room, document, game, canvas, or actor state. The server maintains shared state and broadcasts patches, presence, awareness, or authoritative state changes.

**How only changes change:**  
The shared-state system emits patches for changed fields, objects, actors, or presence records.

**Where changed-knowledge lives:**  
Usually **derived automatically** inside the shared-state protocol, but the shared state schema/model is **hand-maintained**.

**Exemplars:**  
Liveblocks Storage/Presence, PartyKit rooms, Colyseus, multiplayer document/state systems.

**Essential trade-off:**  
Well suited to collaborative/session state, but durable domain querying, permissions, history, and persistence may require additional architecture.

---

### 11. Conditional / Targeted Polling

**What it is:**  
The client periodically checks specific resource versions, ETags, timestamps, or revision counters, then refetches changed resources.

**How only changes change:**  
Unchanged resources return `304`, same revision, or no-op. Changed keys refetch and update subscribers.

**Where changed-knowledge lives:**  
Mostly **hand-maintained** in polling keys and version resources. HTTP cache validation is **derived** at the resource level.

**Exemplars:**  
SWR refetch intervals, TanStack Query polling, HTTP `ETag` / `Last-Modified`, version-counter endpoints.

**Essential trade-off:**  
It can avoid whole-screen refetches, but it is bounded-staleness polling, not true backend-driven instant propagation.

## Client Rendering / Update Granularity

### 1. Fine-Grained Reactivity / Signals

**What it is:**  
State is split into reactive cells. Computations, effects, templates, or DOM bindings track exactly which cells they read. When a cell changes, only dependent computations run.

**How only changes change:**  
A write to one signal updates only DOM bindings/effects that depend on that signal.

**Where changed-knowledge lives:**  
Mostly **derived automatically** by runtime or compiler dependency tracking. Developers still choose signal/store boundaries.

**Exemplars:**  
SolidJS signals, Svelte 5 runes, Vue reactivity, Angular signals, MobX, Knockout.

**Essential trade-off:**  
Very granular updates, but requires mutation discipline and understanding of the dependency graph.

**Difference from virtual DOM:**  
Signals update dependent bindings/effects directly. Virtual DOM systems usually rerun render functions for some component scope, then diff the old and new virtual trees to decide DOM mutations.

---

### 2. Virtual DOM Diffing

**What it is:**  
State changes cause components to render a new virtual tree. The framework compares old and new trees and mutates only differing DOM nodes.

**How only changes change:**  
The real DOM changes are granular, but render computation may still happen for a component subtree unless memoization, selectors, or state boundaries prevent it.

**Where changed-knowledge lives:**  
DOM mutation knowledge is **derived automatically** by the diff. State invalidation boundaries are often **hand-maintained** through component structure, memoization, keys, and subscriptions.

**Exemplars:**  
React, Preact, Vue VDOM mode, Inferno.

**Essential trade-off:**  
Flexible programming model, but “only DOM changed” does not necessarily mean “only dependent computations reran.”

---

### 3. Compiler-Directed DOM Updates

**What it is:**  
A compiler analyzes reactive state usage and emits direct DOM update instructions.

**How only changes change:**  
Assignments or reactive invalidations call generated code for the affected bindings instead of rerendering a whole tree.

**Where changed-knowledge lives:**  
Mostly **derived automatically** by the compiler, within the framework’s reactivity rules.

**Exemplars:**  
Svelte compiled reactivity, Svelte 5 runes, Marko, Qwik-style resumable/compiled boundaries.

**Essential trade-off:**  
Efficient generated updates, but the app follows the compiler’s syntax, constraints, and build model.

---

### 4. Selector / Atom / Entity Store Subscriptions

**What it is:**  
State is partitioned into entities, atoms, selectors, or slices. Components subscribe to the specific slice they need.

**How only changes change:**  
Only subscribers whose selected value changes are notified.

**Where changed-knowledge lives:**  
**Mixed.** Subscription dependency/equality checks may be **derived**. State shape, selectors, entity ids, and normalization are **hand-maintained**.

**Exemplars:**  
Redux with selectors, Reselect, Zustand selectors, Jotai, Recoil, Apollo/Relay stores, TanStack Store.

**Essential trade-off:**  
Good control over render fan-out, but requires disciplined state modeling and selector/equality correctness.

---

### 5. Imperative DOM Mutation / Data Joins

**What it is:**  
Code directly updates DOM nodes or data-bound selections in response to events.

**How only changes change:**  
The developer selects the exact node/list/item and mutates it directly.

**Where changed-knowledge lives:**  
Almost entirely **hand-maintained**.

**Exemplars:**  
Vanilla DOM APIs, jQuery, D3 data joins.

**Essential trade-off:**  
Maximum control, maximum manual correctness burden.

---

### 6. DOM Morphing / HTML Fragment Diffing

**What it is:**  
The client receives or computes an HTML fragment and morphs the existing DOM to match it.

**How only changes change:**  
The morph algorithm preserves matching nodes and mutates only differences.

**Where changed-knowledge lives:**  
DOM differences are **derived** by the morph algorithm. Fragment boundaries and keys/ids may be **hand-maintained**.

**Exemplars:**  
morphdom, Idiomorph, Turbo morphing, Phoenix LiveView client patching.

**Essential trade-off:**  
Can keep server-rendered HTML while avoiding full replacement, but correctness depends on stable ids, fragment boundaries, and preserving stateful DOM nodes.

---

### 7. Retained Scene / Canvas / Visualization Renderers

**What it is:**  
The UI is a retained scene graph or visualization model. Data changes mutate specific scene objects, marks, layers, or buffers.

**How only changes change:**  
Only affected scene objects or data-bound marks are updated/redrawn.

**Where changed-knowledge lives:**  
**Mixed.** Scene graph invalidation may be **derived**. Data-to-object identity is often **hand-maintained**.

**Exemplars:**  
D3 update pattern, Vega runtime, PixiJS, Three.js retained objects.

**Essential trade-off:**  
Useful for dense visual surfaces, but not a general app UI model by itself.

## How The Halves Combine

| Propagation approach | Typical client bridge | Render approach commonly paired | Changed-knowledge location |
|---|---|---|---|
| Reactive query engine | Live query result store | Signals, VDOM, selector stores | Mostly derived end-to-end |
| DB row subscriptions | Normalized cache or local DB | Entity subscriptions, signals, VDOM | DB row change derived; UI mapping hand-maintained |
| Domain event stream | Resource cache, reducers, normalized store | Signals, selector stores, VDOM | Mostly hand-maintained event/resource mapping |
| Delta / patch stream | Local document/object store | Signals, DOM patching, selector stores | Derived if diff/OT/CRDT; hand-maintained if custom patches |
| GraphQL normalized cache | Entity graph cache | Fragment/entity subscriptions, VDOM | Cache deps derived; payload/update policy mixed |
| Keyed invalidation cache | Query/resource cache | Any component model | Key mapping hand-maintained |
| Local-first replica | Local DB/document replica | Observable queries, signals, VDOM | Sync deltas derived; sync scope/conflicts mixed |
| Event log tailing | Local projections/reducers | Selector stores, signals, VDOM | Reducers hand-maintained |
| Server-driven UI | DOM fragments or server component tree | DOM morph/patch | Mixed: targets hand-maintained or patches derived |
| Shared real-time state | Room/document/actor state | Signals, selectors, canvas/scene graph | State patching derived; model hand-maintained |
| Targeted polling | Keyed cache/version store | Any component model | Poll keys hand-maintained |

## Compact Comparison

| Approach | Propagation mechanism | Derived vs hand-maintained | Render granularity | Core trade-off |
|---|---|---|---|---|
| Reactive queries | Subscribe to query results | Derived read/query dependencies | Query result, row delta, or subscribed state | Accept query/runtime constraints |
| DB changefeeds | Row/table/log changes | Derived rows; hand-mapped UI relevance | Row/entity/resource | Storage-level changes are not always domain-level changes |
| Domain events | Typed app events | Hand-maintained schemas and mappings | Entity/resource/projection | Semantic clarity with mapping drift risk |
| Patch streaming | JSON/OT/CRDT operations | Derived or hand-authored patches | Field/path/document op | Ordering/version/base-state complexity |
| Normalized GraphQL cache | Subscription payloads into entity cache | Mixed | Entity field/fragment | Schema and cache policy discipline |
| Keyed cache invalidation | Event invalidates resource keys | Mostly hand-maintained | Resource key | May refetch whole resource |
| Local-first sync | Replica deltas/revisions/ops | Mostly derived by sync engine | Row/doc/query observer | Replica/conflict/sync complexity |
| Event log tailing | Cursor-based event replay | Hand-maintained reducers | Projection slice/entity | Projection evolution and idempotency |
| Server-driven UI | Fragment streams or DOM patches | Mixed | DOM target/subtree | Server owns more UI lifecycle |
| Shared state/actors | Room/document state patches | Mixed | Field/object/actor | Fits collaborative state better than arbitrary querying |
| Targeted polling | Version/ETag checks | Mostly hand-maintained | Resource key | Bounded staleness, not true push |
| Fine-grained signals | Consumes any state update | Derived reactive dependencies | Binding/effect/computation | Dependency graph discipline |
| Virtual DOM | Consumes state updates, diffs render output | Derived DOM diff; hand state boundaries | DOM node, but render subtree may rerun | Reconciliation cost and memoization concerns |
| Selector/atom stores | Consumes cache/local state updates | Mixed | Selector/atom/entity subscriber | State modeling overhead |
| Imperative DOM | Direct event-to-node mutation | Hand-maintained | Exact node | Manual correctness burden |
| DOM morphing | HTML fragment diff/patch | Derived DOM diff; hand boundaries | Fragment/node | Stable ids and state preservation |
| Retained scene renderers | Mutate scene/data objects | Mixed | Object/layer/mark | Specialized rendering model |
