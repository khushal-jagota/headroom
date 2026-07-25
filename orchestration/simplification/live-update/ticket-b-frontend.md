# Ticket B — frontend: TanStack Svelte Query swap + SSE client

Implements plan.md §7 (as revised after plan review; read plan-review-disposition.md too).

## Owns
`web/**` only (never `web/src/vendor/**` beyond mechanical import fallout). No commits — leave the
working tree for the orchestrator to integrate.

## Must not touch
Anything outside `web/`. The ACP/conversation code under `web/src/lib/acp/` and the transcript
pane's WebSocket wire are out of scope (mechanical import fallout only).

## Server contract (being built in parallel — code against it, don't probe the server)
- `GET /api/changes`, `text/event-stream`; unnamed `data: change` frames (EventSource `onmessage`);
  heartbeats are SSE comments (invisible to EventSource).
- `/api/meta` becomes exactly `{test_mode, release_sha}`.

## Deliverables
1. `@tanstack/svelte-query` v6 (runes-based; thunked options). No svelte bump (lockfile already
   5.56.4). Commit-ready `package.json` + `package-lock.json` changes.
2. One `QueryClient` in a plain module; provided above `App.svelte`'s own queries (wrapper from
   `main.ts` or explicit client argument — App consumes the review query itself). Defaults:
   structural sharing, refetchOnWindowFocus, refetchOnReconnect, staleTime 0.
3. Query catalogue module: typed query-options creators with today's keys (plan lists all 13),
   `fetchJson` queryFns, URL-encoded parameterized paths.
4. SSE client module replacing `ws.ts`: EventSource with debounced (250 ms constant)
   `invalidateQueries()` on message; `invalidateQueries()` + status `connected` on open; status
   `reconnecting` on error. Connection pill drops the third `offline` state (judgment call recorded
   in the plan); `App.svelte` adapts. Keep minimal `window.__plannerDebug` (`sseOpens`,
   invalidation flush count) — e2e harness sync primitive.
5. Routes (9) + `App.svelte`: handles → `createQuery`; `mutateJsonWithResourceEffect` and the
   whole effect vocabulary → `mutateJson(path, opts)` = `fetchJson` then awaitable
   `invalidateQueries()`. A failed mutation must not invalidate. Server data must never be written
   into editor/composer component state.
6. Delete `ws.ts`, `resources.svelte.ts`, `resourceCatalogue.ts` and tests `resource-cache`,
   `resource-catalogue`, `ws-connection` (update `package.json` test list). Fix survivors that
   import deleted files or assert the old meta fetch (`vps-status.test.mjs`,
   `acp-production-mount.test.mjs`).
7. New node-style web tests: SSE client (fake EventSource: debounce coalescing,
   invalidate-on-open, status transitions); catalogue keys/paths/encoding; mutation invalidation
   semantics (failure → none; success → awaitable).

## Gates (run through this worktree's `web/`; show output in your report)
- `npm run check --prefix web`
- `npm test --prefix web`
- `npm run build --prefix web`
