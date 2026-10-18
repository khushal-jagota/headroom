# Browser navigation interface

This module supplies the shared browser replica. Production consumers still use their existing Query paths.

Create one collection with `createNavigationCollection()`. Create one owner with `createNavigationOwner({adapter, transport: createNavigationTransport()})` above routes and panes. Call `start()` once. On application disposal, dispose the owner, then destroy the collection.

Only the owner writes canonical navigation records. Consumers must not call collection mutation methods, create optimistic transactions, or write detail responses into this collection.

`createNavigationSelectors(adapter)` supplies a coherent publication from the Svelte live query. A publication contains records, continuity, and the canonical cursor. Metadata and records share one source transaction. Keep publication identity intact. Use `$state.raw` when a Svelte component retains a publication for a later DOM update.

Each mounted render boundary registers one observer with `owner.registerObserver(name)`. Dispose that handle on unmount. A visibility wait names its required observer handles explicitly. The set is fixed for that wait. New route instances register new handles. A removed handle cannot transfer its observation to a new instance.

Subscribe to owner state and make its generation a reactive dependency of the consumer observation effect. Call `observeSelectors(publication, generation)` when that consumer selects the coherent publication. Retain the returned observation token. Call `acknowledgeRender(publication, observation)` only after its actual DOM reflects that same publication. A Svelte `tick()` alone does not establish this fact. Check that the component still represents the captured publication after the tick. Do not acknowledge an old callback after a route change or disposal. An unchanged reconnect requires a fresh observation token for its new generation.

The API trusts the consumer's render acknowledgment. The browser harness checks actual related DOM values and their revision before acknowledgment. New production consumers need equivalent evidence for their own rendered boundary.

For command headers, pass a fresh `responseMetadata` object to `fetchJson` or `mutateJson`. Existing return values, errors, and Query invalidation remain unchanged. Read `Panels-Navigation-Fence` from its returned headers and parse the JSON. Pass the fence to `owner.waitForVisibility(fence, {requiredObservers: [...]})`. Preserve the domain result separately.

Visibility returns `included`, `no_op`, or `unknown`. `included` proves committed history, not that a later action did not replace its value. `no_op` requires server certification. An unknown result never authorizes a command resend. A timeout, removed observer, or disposal releases the wait conservatively.

Connection state stays internal. `subscribed` means that the owner created a stream, not that the server acknowledged a live connection. The owner retains the last complete view during recovery. It closes transport and stops new admission immediately. An already accepted library transaction completes before the publication generation changes. Obsolete requests cannot enter a new source transaction. The owner resumes from the observed published cursor and replaces missing history with a complete snapshot.

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

**NO PUSH AND NO MERGE until the owner inspects and explicitly decides.** No deployment, push-triggered CI, staging/main advancement, cherry-pick, rebase, reset, or patch assembly. Tests, reviews, completion, and agent approvals cannot release this gate.

Migration tests never open `/home/vps/Deployments/Panels/current/data/planner.db` writable. Isolate whole runtimes. Use synthetic databases or consistent copies from explicitly read-only sources. No live fallback or real agent-home fixtures.
