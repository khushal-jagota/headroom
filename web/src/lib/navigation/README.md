# Browser navigation interface

`App.svelte` calls `provideNavigationApplication()` above its routes. The provider creates one scoped owner with `createScopedNavigationOwner()`. It starts that owner on mount and disposes it when the application closes.

`navigationApplication()` reads that context. An isolated component host without the provider receives `undefined`. It never creates another owner.

The owner requests bounded views through `/api/navigation/view/snapshot` and `/api/navigation/view/events`. Mounted consumers register their selected owner and required windows with the owner. The server supplies coherent records, window membership, global counts, and authority for that scope. A replacement scope retains the last coherent display until the new view arrives.

`publication.ts` defines the shared immutable publication. The current scoped owner publishes raw immutable views directly. No TanStack navigation collection or whole-workspace replay client runs beside it. Tests of active consumer projections use a synthetic publication fixture in `web/tests/support/navigation/collection.ts`.

`selectors.effective` exposes fresh, retained, or unknown conversation facts with their publication and read provenance. Unknown has no Boolean value. A loss of runtime authority suppresses runtime knowledge immediately. A late callback from an obsolete scope cannot restore it. Durable message and failure facts remain independent.

The workspace sidebar, Manager headers and child lists, Chief child pages, Review queue, and Ticket masthead use this shared view. Private bodies remain separate detail reads. Ticket controls use that detail directly. The server accepts or refuses each save.

The provider shares one presentation result per effective view identity. A knowledge change invalidates that result even when the publication stays the same. `navigationWorkingAgentCount(view)` reads the global count from the scoped view. Filters and pages do not reduce that total.

The owner carries health separately from its connection state. A degraded snapshot preserves the server availability and reason. A transport or protocol failure reports reconnecting. Only accepted current data for the demanded scope clears the warning. The shell displays that health beside its other connection messages.

Successful owner reads request shared-owner recovery before the unread-message optimization. Failures retain their existing behavior. Current frames from an older active scope cannot clear a warning for its pending replacement.

Review membership groups proposals and unread conversations for each Ticket before page boundaries and counts. A settled Manager excludes only its own messages. An explicit null canonical address records absence. A missing address in an unrelated page does not establish removal.

The server fences snapshots, streams, and response sends with the existing producer authority. Scoped delivery shares admission, retention, and source availability with the navigation runtime. Migration tests use synthetic databases or consistent read-only copies. They never open the live database writable.

The browser uses server shapes directly. `identity.ts` supplies revision comparison, authority identity, and record keys without a second schema validator. Generate shared types with `scripts/generate_navigation_browser_contracts.py`. Run the scoped owner and consumer tests in `web/tests/navigation-views.test.ts` and `web/tests/navigation-application.test.ts`.
