# Accepted implementation plan

1. Extend `App.svelte` route parsing so `#/workspace/<ticket-id>` carries an optional ticket ID into Workspace while preserving `#/workspace` and the existing board alias.
2. Refactor `BoardRoute.svelte` so the routed ID drives the selected card and inspector; card clicks push the ticket route, while Chief of Staff and ticket deletion clear it back to `#/workspace`.
3. Resolve the routed selection after board data loads and fall back to the normal Chief of Staff view for a missing or unknown ticket ID.
4. Add Playwright coverage for plain and invalid routes, direct ticket load and refresh, click-driven URL updates, ticket switching, and browser back/forward; run `./verify` once after review and integration.
