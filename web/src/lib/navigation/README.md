# Browser navigation interface

This module supplies the shared browser replica. `App.svelte` calls `provideNavigationApplication()` above its routes. The provider creates one collection, transport, owner, and coherent selector set. It starts the owner on mount and disposes the owner before the collection on application destruction.

`navigationApplication()` reads that context. An isolated component host without the provider receives `undefined`. It never creates another owner. Every successful existing `advanceOwnerRead` response triggers shared-owner recovery before the unread-message optimization or current-conversation check. Failed reads retain their existing behavior.

Production summary consumers still use their existing Query paths. Their adoption and actual DOM proof remain pending. The shared core now accepts schema4 (`attention-knowledge-v4`) through wire2. This prerequisite does not establish a completed cutover, UI parity, or application speed improvement.

Snapshots have current or degraded mode. A current response contains complete attention, an exact authority profile, and a replay cursor. A degraded response contains coherent durable attention only, with a null cursor and proof. Every batch carries the same complete authority profile as its accepted current snapshot. Producer permit scope and navigation cursor scope are independent namespaces. Authority checks precede duplicate suppression.

Collection records and publication metadata enter one TanStack transaction. Metadata includes mode, authority, nullable cursor, and a local admission token. That token identifies pending publication independently of cursor equality. `selectors.effective` exposes the shared fresh, retained, or unknown conversation facts with exact publication and read-boundary provenance. Unknown has no Boolean value. An observed loss suppresses runtime knowledge synchronously before an admitted collection transaction drains. A late old commit cannot restore runtime knowledge.

Durable message and failure knowledge remain independent. `owner.invalidateDurableFact` invalidates only the fact affected by a confirmed operation. An observation must cover its canonical owner-read or error-acknowledgment watermark before it restores fresh knowledge. A late read response preserves a newer fresh observation that already covers that read. No read, failure-acknowledgment, or Signal policy changes here.

Create one collection with `createNavigationCollection()`. Create one owner with `createNavigationOwner({adapter, transport: createNavigationTransport()})` above routes and panes. Call `start()` once. On application disposal, dispose the owner, then destroy the collection.

Only the owner writes canonical navigation records. Consumers must not call collection mutation methods, create optimistic transactions, or write detail responses into this collection.

`createNavigationSelectors(adapter)` supplies a coherent publication from the Svelte live query. A publication contains records, continuity, and the canonical cursor. Metadata and records share one source transaction. Keep publication identity intact. Use `$state.raw` when a Svelte component retains a publication for a later DOM update.

Each mounted render boundary registers one observer with `owner.registerObserver(name)`. Dispose that handle on unmount. A visibility wait names its required observer handles explicitly. The set is fixed for that wait. New route instances register new handles. A removed handle cannot transfer its observation to a new instance.

Subscribe to owner state and make its generation a reactive dependency of the consumer observation effect. Call `observeSelectors(publication, generation)` when that consumer selects the coherent publication. Retain the returned observation token. Call `acknowledgeRender(publication, observation)` only after its actual DOM reflects that same publication. A Svelte `tick()` alone does not establish this fact. Check that the component still represents the captured publication after the tick. Do not acknowledge an old callback after a route change or disposal. An unchanged reconnect requires a fresh observation token for its new generation.

The API trusts the consumer's render acknowledgment. The browser harness checks actual related DOM values and their revision before acknowledgment. New production consumers need equivalent evidence for their own rendered boundary.

For command headers, pass a fresh `responseMetadata` object to `fetchJson` or `mutateJson`. Existing return values, errors, and Query invalidation remain unchanged. Read `Panels-Navigation-Fence` from its returned headers and parse the JSON. Pass the fence to `owner.waitForVisibility(fence, {requiredObservers: [...]})`. Preserve the domain result separately.

Visibility returns `included`, `no_op`, or `unknown`. `included` proves committed history, not that a later action did not replace its value. `no_op` requires server certification. An unknown result never authorizes a command resend. A timeout, removed observer, or disposal releases the wait conservatively.

Connection state stays internal. `subscribed` means that the owner created a stream, not that the server acknowledged a live connection. The owner retains the last complete view during recovery. It closes transport and stops new admission immediately. The local generation and runtime gate change before an already admitted library transaction drains. Obsolete requests cannot enter a new source transaction. Recovery requires a complete current snapshot before a new stream starts. Degraded snapshots retry through the existing recovery schedule and never resume a stream or certify command visibility.

An atomic local publication number distinguishes identical replacement snapshots. It does not advance the server revision. This identity lets live selectors observe a new certified snapshot even when its facts match the previous view.

Generate types with the worktree interpreter:

```sh
.venv/bin/python scripts/generate_navigation_browser_contracts.py
.venv/bin/python scripts/generate_navigation_browser_contracts.py --check
```

Run focused browser verification:

```sh
cd web
npm run test:vitest -- tests/navigation-contract.test.ts tests/navigation-collection.test.ts tests/navigation-owner.test.ts tests/navigation-transport.test.ts
node tests/navigation-browser.test.mjs
```

The harness uses an isolated synthetic runtime. Its comparator is not the original browser baseline. Production consumer parity and the complete baseline/candidate performance comparison remain downstream.

The owner permits merges of staging into the local feature branch, local conflict resolution, implementation, and isolated validation. No rebuild push or merge into staging/main, deployment, or push-triggered CI is authorized. Retain the local candidate for owner inspection. Tests, reviews, completion, and agent approvals do not authorize publication. No rebase, cherry-pick, reset, or patch assembly is authorized.

Migration tests never open `/home/vps/Deployments/Panels/current/data/planner.db` writable. Isolate whole runtimes. Use synthetic databases or consistent copies from explicitly read-only sources. No live fallback or real agent-home fixtures.
