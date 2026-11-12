# Shared navigation engine

Navigation owns every record derivation and one shared view engine. Later domains do not register navigation sources or implement views. Their writes use the write door. Existing source triggers populate `navigation_dirty`; the navigation commit step resolves dependencies, derives all affected roots, updates records, journals the revision, and updates the shared projection.

`data/derivation.ts` covers Tickets, Managers, Chief, Projects, goals, conversations, addresses, and totals. It reads canonical source rows. `views/projection.ts` owns memberships, counts, owner relationships, and change footprints. `views/read.ts` owns bounded windows, closures, Review context, paging, overlap, and recap references.

## Ordering and bounded reads

Intl.Collator compares the same title and identity operands as Python's ICU order. Its generation is separate because Node does not expose ICU sort-key bytes. Existing projection tables retain semantic comparator operands. The TS-only `navigation_view_ordered` index stores physical positions for indexed page reads. Public anchors retain semantic operands, not physical positions. Insertions, deletions, and title changes do not turn an old anchor into an ordinal offset.

Count-only windows load no membership rows. Page reads fetch only admitted rows and logarithmic anchor-search points. Dynamic Review priority uses the bounded submitted history keys. Projection writes rebuild the order of groups whose membership or sort operands changed. The write cost is O(N log N) sort plus N replacements for each changed group. Known follow-up: a changed group is sorted again in full inside the write transaction. The measured cost is 313 ms at 10,000 members, per changed group. Gate 3 measures this on a copy of live before anyone optimizes it.

## Publication and delivery

The shared startup creates `services.signalAuthority` under runtime ownership. Navigation stores and validates that supplied installation inside each read snapshot. It creates no producer permit. The idle callback retains durable unread/failure facts, but runtime-only working/question states remain false. The real conversation core replaces that temporary source.

Delivery follows the Python scoped coordinator: poll committed change footprints, recapture intersecting views, emit patch or checkpoint, send heartbeats, and retain bounded journal history. Epoch changes, missing retained footprints, and revoked authority close the connection. Reconnection starts a fresh snapshot, as Python does; Last-Event-ID is not a replay contract here. Snapshot sends retain their authority through asynchronous send hooks. Content reads validate the durable recap digest and stream bounded Unicode chunks.

## Adding later domain writers

No navigation registration is required. Preserve source trigger declarations and parameter names, write through the shared door, and extend that domain's comparison list with snapshots after writes. Add the domain's records to populated restart evidence. Gate 3 still proves the combined result on a copy of live.

Resource providers remain separate. Implement `ResourceProvider` from `server/resource_versions/contracts.ts`, add it to that registry, and prove recognized keys, body coherence, revisions, and authority in its domain list. This unit proves the generic registry with no providers.
