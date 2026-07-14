# t_ycpcb619 — Show live Panels server connection status in navigation

## Accepted success

Panels’ navigation always shows a compact, truthful live connection state — Connected, Reconnecting, or Offline — based on the browser’s current event connection and liveness, not worker activity or the time of the last successful fetch. If that connection fails while the page remains open, the status changes promptly without a reload; Panels reconnects automatically, clears the warning when healthy, and reconciles canonical resources so the UI cannot remain silently stale. Deterministic browser coverage proves the full connected → loss → reconnect and reconciliation flow while existing navigation and keyed event invalidation remain intact.

## Accepted approach

Use the existing event WebSocket as the single owner of browser-to-server health. Add a lightweight server heartbeat for quiet periods, expose its lifecycle to the Svelte shell as Connected, Reconnecting, or Offline, and keep the current bounded reconnect backoff. After a confirmed reconnection, retain cursor-based event catch-up and perform one reconciliation refresh of the catalogue resources currently in use, rather than rebuilding the screen or replacing keyed invalidation. Render the compact status beside — but clearly separate from — worker presence, and prove the transitions, recovery, and refreshed state with deterministic WebSocket-controlled browser tests.

## Accepted plan

1. Finalize the compact, accessible shell treatment shown in the tracked planning artifact: keep it at the navigation’s right edge, preserve existing links, and separate it from worker presence across desktop and mobile.
2. Add quiet-period heartbeat frames to the server event tailer, with focused tests covering heartbeat delivery, ordinary event batches and cursor behavior, and clean disconnect handling.
3. Turn the browser event-stream module into the single reactive connection-state owner: define and test Connected, Reconnecting, and Offline transitions; enforce a missed-heartbeat deadline; preserve bounded retry backoff; and clean up every timer and socket.
4. Add a recovery-only catalogue reconciliation that refreshes currently active canonical resources once a connection is re-established, while retaining cursor catch-up and the existing event-to-key invalidation path.
5. Add deterministic Playwright coverage that holds the page open through connection loss and recovery, verifies each navigation state without reload, changes server data during the interruption, and proves reconnection refreshes the visible canonical state. Update the frontend documentation, rebuild the served bundle, and finish with one clean `./verify` run.

## Implementation contract resolved by plan review

- **Heartbeat wire shape:** a quiet heartbeat is the existing event-batch envelope with `events: []` and the connection’s current `cursor`. It never advances the cursor, maps event keys, schedules a UI flush, or invalidates a resource. Any valid event batch or heartbeat confirms liveness.
- **State transitions:** the shell starts in **Reconnecting** and stays there through socket open until the first valid frame. A valid frame changes it to **Connected**, resets the bounded retry delay, and restarts the missed-heartbeat deadline. Socket close or a missed-heartbeat deadline changes a previously healthy connection immediately to **Reconnecting** and starts automatic retries. If no valid frame arrives for three heartbeat intervals, the state becomes **Offline**; retries continue indefinitely with the existing maximum delay, so there is no exhausted terminal state. A later valid frame returns to **Connected** without reload.
- **Timing ownership:** the server has one explicit heartbeat cadence exposed through `/api/meta`; the browser derives its missed-heartbeat and Offline deadlines from that served value. Tests override the cadence rather than waiting on production timing.
- **Recovery ownership:** `resourceCatalogue.ts` exposes the recovery reconciliation API. It identifies and refreshes only catalogue resources with current subscribers through a narrow generic cache query; `ws.ts` never reads cache internals or debug stats. Reconciliation runs once after a connection that was previously confirmed healthy is confirmed healthy again, not on initial startup and not on every heartbeat.
- **Separate recovery proofs:** browser coverage proves (a) reconnect retains the prior cursor and maps missed events through the existing `keysForEvent` path, and (b) the one-time recovery reconciliation refreshes another subscribed resource even when no matching event arrives. It also proves no page reload or shell remount.
- **Responsive shell:** both worker presence and connection health remain visible and distinct on mobile. The nav may scroll as it already does; the status group remains compact rather than deleting either signal.

## Constraints

- The client may report only what its own browser-to-server connection proves; it must not claim knowledge of the server process.
- Worker presence and server connection health remain different signals.
- Preserve existing navigation, event-cursor catch-up, keyed invalidation, and the server as canonical state owner.
- No page reload and no monitoring dashboard.
- Implementation ends with a verified commit on this ticket branch. Merge, deployment, live restart, and cleanup belong to Closeout.

## Review artifact

- `connection-status-plan.html` is the tracked review copy of the approved managed planning artifact.
