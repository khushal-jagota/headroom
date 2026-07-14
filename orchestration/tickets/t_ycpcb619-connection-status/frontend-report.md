# Frontend connection-status slice report

## Changed files

- `web/src/lib/resources.svelte.ts`
- `web/src/lib/resourceCatalogue.ts`
- `web/src/lib/ws.ts`
- `web/src/App.svelte`
- `assets/app.css`
- `web/tests/resource-cache.test.mjs`
- `web/tests/resource-catalogue.test.mjs`
- `web/tests/ws-connection.test.mjs`
- `web/package.json`
- `tests/e2e/conftest.py`
- `tests/e2e/test_connection_status.py`
- `docs/frontend.md`
- `web/dist/index.html`
- `web/dist/assets/index-BdxCKt4n.js`
- removed old built chunk `web/dist/assets/index-Lgae3D_0.js`

No backend production files, `PROGRESS.md`, `decisions.md`, commits, merges, deploys, or live state were touched by this frontend slice.

## RED evidence

Focused frontend cache/catalogue RED:

```sh
npm --prefix web test -- --resource-recovery-red
```

Result:

```text
TypeError: reconcileSubscribedCatalogueResources is not a function
```

Focused Playwright RED attempt before WebSocket production edits:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/pytest tests/e2e/test_connection_status.py::test_connection_status_recovers_with_cursor_replay_and_subscribed_reconciliation -q
```

Result: blocked before app code by the local browser sandbox:

```text
BrowserType.launch: Target page, context or browser has been closed
FATAL:base/apple/mach_port_rendezvous_mac.cc:159
bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

The same launch blocker repeated after implementation, so focused Playwright could not reach the app in this sandbox.

## GREEN evidence

Focused frontend cache/catalogue GREEN:

```sh
npm --prefix web test -- --resource-recovery-green
```

```text
resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
```

Full frontend unit suite:

```sh
npm --prefix web test
```

```text
resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
[planner] ws open since=0
[planner] flush 1
[planner] ws open since=1
ws-connection.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
```

Svelte/TypeScript check:

```sh
npm --prefix web run check
```

```text
svelte-check found 0 errors and 0 warnings
```

Build:

```sh
npm --prefix web run build
```

```text
✓ 154 modules transformed.
✓ built in 569ms
```

Ruff for changed Python E2E files:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/ruff check tests/e2e/conftest.py tests/e2e/test_connection_status.py
```

```text
All checks passed!
```

## Behavior implemented

- The generic cache now exposes `subscribedResourceKeys()`, a narrow query containing only currently subscribed cache keys.
- `resourceCatalogue.ts` owns recovery reconciliation through `reconcileSubscribedCatalogueResources()`, filtering to known catalogue identities and refreshing those resources once.
- `ws.ts` owns connection state, sockets, retry timers, heartbeat deadlines, and recovery reconciliation triggers.
- Socket open alone does not set Connected; only a valid `{ events, cursor }` frame does.
- Empty heartbeat frames update liveness only and do not update cursor, map event keys, schedule a flush, or invalidate resources.
- Retry backoff remains capped at 10 seconds and continues after Offline, including open-but-silent retry sockets.
- Reconnection after a previously healthy connection runs one catalogue reconciliation while preserving cursor catch-up and keyed invalidation for event batches.
- `App.svelte` renders a separate accessible live connection status beside worker presence.
- `tests/e2e/conftest.py` sets `PLAN_WS_HEARTBEAT_MS=500` and asserts the served meta value for deterministic browser timing.
- `docs/frontend.md` documents the shell connection-health contract.
- `web/dist` was rebuilt.

## Parent integration and review correction

The focused Playwright case was rerun outside the Codex sandbox and passes in Chromium. It records the complete `reconnecting → connected → reconnecting → offline → connected` sequence, cursor replay from the prior confirmed cursor, keyed event invalidation, one subscribed-resource reconciliation for state changed without an event frame, stable screen identity, and desktop/mobile status geometry.

Independent implementation review found that callbacks from a superseded WebSocket could still change global health, cursor, invalidations, or retry state. A deterministic fake-WebSocket regression was added first and failed with stale callbacks leaving the state `reconnecting`; guards were then added to `onopen`, `onmessage`, and `onclose`, after which `ws-connection.test.mjs` passes. The final production bundle is `index-BdxCKt4n.js` and the e2e heartbeat fixture is 500 ms.
