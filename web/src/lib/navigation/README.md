# Browser navigation interface

This module supplies the shared browser replica. `App.svelte` calls `provideNavigationApplication()` above its routes. The provider creates one collection, transport, owner, and coherent selector set. It starts the owner on mount and disposes the owner before the collection on application destruction.

`navigationApplication()` reads that context. An isolated component host without the provider receives `undefined`. It never creates another owner. Every successful existing `advanceOwnerRead` response triggers shared-owner recovery before the unread-message optimization or current-conversation check. Failed reads retain their existing behavior.

The workspace sidebar, ordinary Manager headers and child lists, Chief child pagination, Review queue, Ticket masthead and current-stage section, and Chief conversation pointer use the shared view. Ordinary Manager body, artifacts, and conversation history use a separate content-only read. Proposal bodies use a detail query keyed by the exact published proposal identity. These consumer changes do not by themselves establish complete application recovery, UI parity, or a speed improvement.

The provider shares one presentation result per effective view identity. One pass indexes parent membership, identity joins, addresses, and Review facts. A knowledge-only change creates a new effective view and invalidates that result even when the publication stays the same.

Feature modules own their DOM assertions and command dependencies. The application captures relevant mounted observer instances before a command starts. It retains bounded command evidence separately from the original result. Visibility can be included, no-op, or unknown. It never delays domain success or retries a command. Missing source or uncharacterized effects leave visibility unknown.

The Ticket current-stage section admits private detail only when its public identity, gate, proposal revision, and control facts match the shared record. Worker configuration must describe that exact stage. The retained section preserves its original detail and local edits. It cannot certify a newer publication or authorize a gesture against a replaced identity. Private bodies remain detail reads.

The Ticket masthead joins the assigned Manager's Project when a parent exists. Otherwise it uses the Ticket's own Project. Its title, type, and exact current conversation mark remain separate from the detail object that authorizes actions and supplies bodies. A retained header after deletion cannot acknowledge a current publication. The shell reports navigation health. Independent detail or content errors keep their own existing error lines.

The scoped owner carries client health separately from its mechanical connection state.
A degraded snapshot preserves the server's availability and reason through the existing retry sequence.
A transport or protocol failure reports reconnecting. Only accepted current data for the demanded scope clears the warning.
Current frames from an older active scope cannot clear a warning for its pending replacement.
Obsolete callbacks cannot update health. Rejected authority cannot supply a degraded reason or restore healthy status.
Generation changes, runtime knowledge loss, and retry timing retain their existing rules.
The application passes this health to both shell status mounts, alongside deployment and update messages.

`navigationWorkingAgentCount(view)` in `presentation.ts` supplies the shared workspace-wide count as a number or `null`. It accepts an effective view, `null`, or `undefined`. An absent view or lost runtime authority returns `null`. A scoped view returns `counts.running_conversations` directly, including zero or `null`, without a resident-record scan. Filters and pagination do not reduce this global total.

For a complete publication, the API deduplicates current Ticket, Chief, and Manager conversation pointers. It counts only fresh `working: true` facts. A missing, unknown, or retained working fact makes the total `null`. Historical-only and unlinked conversations do not contribute. `message_for_owner` does not affect the count.

Review membership groups each Ticket's eligible proposal and unread conversations before page boundaries and counts.
Message-only Tickets also use Ticket member keys. Manager and Chief cards retain address member keys.
Each Ticket closure includes all eligible canonical addresses and exact conversations, with atomic budget admission.
Review totals come from grouped membership. Ordinary unread membership and its distinct-conversation totals stay independent.
A settled Manager excludes only its own messages. Ticket eligibility uses its own done and backlog state.
The earliest constituent sets the Ticket card's queue position, deliberately bringing a new proposal forward beside an older message.
Proposal timestamps and revisions remain separate approval identities. Address selection precedes owner-state filtering.
Painted Review conversations request bounded canonical-owner context while a reply or read resolves.
The server supplies the canonical address and owner even when unread membership clears or that owner becomes excluded.
An explicit null address records absence. A missing address in an unrelated page does not establish canonical removal.
This context preserves uncertain reply attempts without retaining a conversation after its canonical owner becomes excluded.
It includes only the requested conversation identities and follows existing record, byte, and request budgets.

The existing Review field `runningConversationCount` delegates to this API. The exact unfiltered Worker out-claim sum remains separate. The API adds no display or placement. A pending collection handoff retains the last coherent display without dropping local proposal edits. Explicit authority loss still suppresses runtime knowledge.

Snapshots have current or degraded mode. A current response contains complete attention, an exact authority profile, and a replay cursor. A degraded response contains coherent durable attention only, with a null cursor and proof. Every batch carries the same complete authority profile as its accepted current snapshot. Producer permit scope and navigation cursor scope are independent namespaces. Authority checks precede duplicate suppression.

Collection records and publication metadata enter one TanStack transaction. Metadata includes mode, authority, nullable cursor, and a local admission token. That token identifies pending publication independently of cursor equality. `selectors.effective` exposes the shared fresh, retained, or unknown conversation facts with exact publication and read-boundary provenance. Unknown has no Boolean value. An observed loss suppresses runtime knowledge synchronously before an admitted collection transaction drains. A late old commit cannot restore runtime knowledge.

Durable message and failure knowledge remain independent. `owner.invalidateDurableFact` invalidates only the fact affected by a confirmed operation. An observation must cover its canonical owner-read or error-acknowledgment watermark before it restores fresh knowledge. A late read response preserves a newer fresh observation that already covers that read. No read, failure-acknowledgment, or Signal policy changes here.

Create one collection with `createNavigationCollection()`. Create one owner with `createNavigationOwner({adapter, transport: createNavigationTransport()})` above routes and panes. Call `start()` once. On application disposal, dispose the owner, then destroy the collection.

Only the owner writes canonical navigation records. Consumers must not call collection mutation methods, create optimistic transactions, or write detail responses into this collection.

`createNavigationSelectors(adapter)` supplies a coherent publication from the Svelte live query. A publication contains records, continuity, and the canonical cursor. Metadata and records share one source transaction. Keep publication identity intact. Use `$state.raw` when a Svelte component retains a publication for a later DOM update.

Application consumers register genuine observer handles with `application.registerRenderBoundary(name, matchesImpact)` and dispose them with `disposeRenderBoundary`. Feature predicates describe dependencies on canonical record fields. The application captures matching mounted handles before the command request. The required set stays fixed for that wait. New route instances register new handles. A removed handle cannot transfer its observation to a new instance.

Subscribe to owner state and make its generation a reactive dependency of the consumer observation effect. Call `observeSelectors(publication, generation)` when that consumer selects the coherent publication. Retain the returned observation token. Call `acknowledgeRender(publication, observation)` only after its actual DOM reflects that same publication. A Svelte `tick()` alone does not establish this fact. Check that the component still represents the captured publication after the tick. Do not acknowledge an old callback after a route change or disposal. An unchanged reconnect requires a fresh observation token for its new generation.

The API trusts the consumer's render acknowledgment. The browser harness checks actual related DOM values and their revision before acknowledgment. New production consumers need equivalent evidence for their own rendered boundary.

Use `application.runCommand(impact, request, label)` for commands. Pass the callback's `responseMetadata` object to `fetchJson` or `mutateJson`. The application parses fence headers and retains bounded visibility outcomes, captured observer identities, generation, publication, and timestamps. Existing return values, errors, and Query invalidation remain unchanged. Read evidence through `commandEvidence` or `subscribeCommandEvidence`. Missing source or uncharacterized effects require an incomplete impact. Never infer successful display from the domain result.

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
```

The existing order, rail, child, and guidance browser hosts provide bounded consumer DOM evidence with synthetic sources. `scripts/navigation_browser_harness.py` now runs the actual built App and application lifetime with isolated synthetic source data and controlled backend adapters. It compares the original production browser with the candidate. It preserves source, dependency and asset hashes, raw network events, DOM observations and measurement limits. The older `navigation-browser.test.mjs` comparator remains separate supporting evidence. A harness run alone does not establish complete product acceptance.

The owner permits local incorporation, implementation, and isolated validation under the recorded source dispositions. No push of any kind, staging/main merge or publication, deployment, or push-triggered CI is authorized. Retain the local candidate for owner inspection. Tests, reviews, completion, and agent approvals do not authorize publication. No rebase, cherry-pick, reset, or patch assembly is authorized.

Migration tests never open `/home/vps/Deployments/Panels/current/data/planner.db` writable. Isolate whole runtimes. Use synthetic databases or consistent copies from explicitly read-only sources. No live fallback or real agent-home fixtures.
