# AD08 — Deep frontend Resource Catalogue

## Outcome

Replace the current scattered cached-read declarations and free-form mutation invalidation arrays with one
frontend Resource Catalogue. The catalogue owns every cached server projection's identity, read location,
response type, event dependencies, and refresh policy. It also owns the named affected-resource effect of
each successful UI mutation. Routes and components consume catalogue entries; they do not reconstruct keys,
URLs, fetchers, dependency rules, or key arrays.

This is a frontend data-boundary deepening, not a new client-side source of truth. The server remains
canonical. The two correctness signals stay distinct and both remain:

1. the typed server event stream is the canonical invalidation signal; and
2. a successful UI write immediately refreshes only its catalogue-owned affected projections, so the user
   sees the result without waiting for the socket and remains correct if the socket misses or disconnects.

No UI, backend, HTTP, event, response, database, domain, or timing behavior changes.

## Binding decisions and contracts

- `PRINCIPLES.md` and `AGENTS.md`, especially exact naming, earned mechanisms, targeted invalidation, and
  `events -> keyed invalidation -> targeted refetch`.
- `D-resource-refresh-two-signals`: canonical events plus immediate targeted refresh; routes own no free-form
  invalidation lists; Review advance awaits one catalogue-owned refresh rather than launching duplicate
  reads.
- `D-resource-catalogue-scope`: catalogue cached UI reads only. Mutations, uploads, managed-file reads,
  copy-text, and startup `/api/meta` remain ordinary requests.
- `D-frontend-reactivity`: an entity prefix is the primary complete event rule; a new event kind about an
  existing entity needs no new ordinary mapping. Kind/payload exceptions exist only where one event affects
  another entity or where an aggregate genuinely has a narrower dependency. The completeness backstop stays
  mandatory.
- AD04's Ticket-only Review contract: Review invalidates only for its actual Ticket/current-day inputs;
  Sprint-item-only events and unrelated Ticket changes do not refresh it. Accept and return-for-revision
  each await one Review refresh after the mutation, with no duplicate expected-invalidation read.
- AD05–AD06's Chat contracts: Panels Chat state remains a cached server projection, while Chat mutations,
  image uploads, active-turn polling, Pause, and server-owned turn behavior remain exact.
- AD07's Managed Markdown boundary remains untouched.
- The owner's standing Worker-type ruling remains exact: live Ticket creation has no implicit Worker-type
  default; only explicit migrations may classify historical rows. AD08 changes no Ticket ingress or storage.

## Current shallow boundary to replace

Today the generic cache engine in `web/src/lib/resources.svelte.ts` is sound, but cached resource identity is
redeclared at every use:

- `App.svelte`, eight route components, `ChatPanel.svelte`, and `manifest.svelte.ts` each construct a resource
  key and fetcher together;
- the same Ticket, Review, Projects, current Sprint, Chat status, and manifest reads are repeated across
  callers;
- six route components pass literal or locally assembled invalidation-key arrays to `mutateJson`;
- Review wraps some mutations in a second manual refresh to obtain ordering, while its route still knows
  which other resources the mutation affects;
- `eventMapping.mjs` owns a second resource-name vocabulary and currently emits some identities for reads the
  UI does not cache; and
- tests inspect route source to prove array contents, so they protect duplication rather than the intended
  catalogue boundary.

The result works but is shallow: any route can invent a cached identity, use a different endpoint for an
existing key, or forget one half of the two-signal refresh contract.

## Catalogue scope

Inventory every production `resource(...)`, `peek`, `invalidate`, `invalidateMany`, `.refresh()`, and
`mutateJson(..., expectedInvalidations)` call before fixing declarations. The production catalogue contains
exactly the cached server projections actually used after this ticket:

- Board;
- Review;
- today's Day;
- backlog Sprint items;
- Ideas;
- Projects;
- Sprint summaries;
- current Sprint;
- one Ticket by id;
- Panels Chat state by entity id;
- Chat gateway status by entity id;
- Chat command catalogue; and
- Worker-type manifests.

Do not add parameterized resources merely because the event mapper currently emits their keys. In
particular, a dated Day, individual Sprint item, and individual Sprint are not catalogue entries unless the
implementation inventory proves a production cached read exists. Remove invalidation identities that can
only target nonexistent cache entries; preserve the affected real aggregate refresh instead.

Every catalogue entry must expose one typed way to obtain its `ResourceHandle` and own:

1. its stable identity/key, including parameter normalization for Ticket or Chat entity ids;
2. its exact GET path and response type;
3. which entity prefixes and exceptional event payloads can change it;
4. whether it participates in event invalidation, manual/poll refresh only, or both; and
5. any context needed for correct matching, especially today's resolved Day id and cold-start behavior.

The generic cache engine continues to own deduplication, force refresh, stale response suppression,
last-good-data retention, subscriber counting, disposal, and structured errors. The catalogue must use that
engine rather than reimplement it.

## Event invalidation contract

There is one resource identity vocabulary and one dependency source. `keysForEvent` must return only valid
catalogue resource identities. It may delegate to catalogue declarations or compiled matchers, but must not
retain a second hand-written list of equivalent resource names.

The primary rule remains entity based: adding an ordinary new `EventKind` about an already-known entity
cannot require a kind-list edit to invalidate that entity's directly affected cached resource and ordinary
aggregates. Explicit kind/payload logic remains only where entity identity is insufficient, including:

- link changes and `affected_blocked_target_ids`, which affect both named endpoints and their real aggregates;
- Day membership events carrying `ticket_id`, which affect the named Ticket and Board;
- current-day membership, which affects Review only for today's Day, including cold start before today's id
  is cached;
- Chat message and turn events, which affect only the matching Panels Chat state;
- `chat_session_created`, whose currently shared Ticket session field affects both the matching Ticket and
  Panels Chat projections until AD09 separates Employee session history; and
- `project_updated` when its payload includes `name`, which affects every aggregate projection that embeds
  Project names plus already-cached matching Ticket details; and
- Review's deliberately narrower Ticket-event/title dependency from AD04.

Preserve the current event debounce, batching, cursor, reconnect, unknown-prefix failure, and one cached-today
lookup per event behavior unless the reviewed plan proves a simpler equivalent. Do not invalidate Chat
history from unrelated Ticket events merely because both share a Ticket entity id.

The anti-rot test must fail when:

- a backend `EventKind` maps to no real catalogue resource;
- an event uses an unknown entity prefix;
- event mapping returns an identity the catalogue cannot recognize;
- a production cached read is created outside the catalogue; or
- a catalogue event dependency is unreachable from the mapper.

A kind with only ordinary entity behavior passes by the primary rule; do not create a ceremonial
per-kind table just to satisfy the test.

## Immediate mutation effects

Keep mutations as ordinary typed HTTP operations, but replace every free-form expected-invalidation array
with a named, typed catalogue effect. Freeze the exact effect declarations in the delegated plan after
inventorying all current UI mutations.

At minimum preserve these current distinctions:

- Idea creation affects Ideas;
- backlog Sprint-item creation affects backlog, current Sprint, and Board;
- ordinary Ticket edits, scope, notes, values, and recap affect that Ticket, Board, and current Sprint;
- a Ticket title edit additionally affects Review;
- Ticket accept and takeover/release additionally affect Review;
- Review accept and return-for-revision refresh the named Ticket/Chat/aggregates immediately, then await
  exactly one catalogue-owned Review refresh after success;
- Review title save includes Review in the immediate effect;
- today's Day edit refreshes the today's-Day projection without a nonexistent dated-Day cache entry; and
- current Sprint document edit refreshes current Sprint and Sprint summaries without a nonexistent
  individual-Sprint cache entry.

An effect may take a Ticket or Chat id, but callers never pass resource keys. Unknown effect names or missing
required ids are type errors, not runtime fallback. A failed HTTP mutation triggers no invalidation or
refresh. Preserve non-optimistic writes.

Chat start, image upload, Pause, active-turn polling, initial active-state refresh, Review stale-self-heal,
and other explicit read refreshes keep their current ordering and error behavior. The plan must state which
become catalogue refresh-policy operations and which remain ordinary calls on a typed catalogue handle.

## Required RED-first acceptance

Add or replace focused frontend contract tests before rewiring production. Prove all of the following:

1. **One catalogue.** Every production cached projection is declared once in the catalogue. No route,
   component, manifest helper, event mapper, or WebSocket owner calls the generic `resource` factory with a
   hand-built key/fetcher or embeds a second cached-read path.
2. **Typed read parity.** Every caller receives the same response type, key sharing, endpoint, initial-load,
   background-refresh, disposal, and error behavior as today. The App and Review screen share the same Review
   cache entry; repeated Projects/current-Sprint/manifest/Chat-status consumers share their entries.
3. **One mutation-effect vocabulary.** No route contains a resource-key invalidation array. Every current UI
   mutation selects the exact named effect and parameter set, failed writes do nothing, and Review advance /
   return each await one and only one Review refresh.
4. **Two-signal correctness.** A successful write refreshes its affected visible projection immediately even
   with the event socket absent, and a later matching event remains idempotent and targeted. Unrelated cached
   resources do not fetch.
5. **Event completeness.** Every canonical backend event kind maps through the entity-first rules to at least
   one real catalogue identity; unknown prefixes and unrecognized returned identities fail. A synthetic new
   kind on each known entity prefix works without a kind-list edit.
6. **Dependency precision.** Link endpoints, blocked-target payloads, current-day membership, Review's narrow
   inputs, Chat-only message/turn events, the shared `chat_session_created` Ticket/Chat dependency, Project
   name propagation, Sprint items, Projects, Ideas, Sprints, Ticket details, Board, and current Sprint retain
   their exact current affected sets after nonexistent keys are removed.
7. **Cache-engine preservation.** Concurrent fetch deduplication, forced-refresh supersession, stale-response
   ignoring, last-good-data retention, subscriber refetch, disposal, structured error propagation, and
   batch de-duplication remain covered; add focused engine tests if the existing suite does not prove them.
8. **Browser preservation.** Existing live invalidation flows still update Board, Review/nav badge, Ticket,
   Day, Sprint, Backlog, Ideas, Projects, Chat state, and Worker manifest consumers without reload or
   whole-screen refetch. Chat streaming/activity remains stable through unrelated invalidations.

Static tests should assert ownership and deleted free-form shapes without coupling to incidental formatting.
Do not preserve old arrays or helper aliases to make source-regex tests pass; replace those tests with the
catalogue contract.

## Deletion and documentation

Delete the free-form `expectedInvalidations: string[]` mutation surface, route-local invalidation arrays,
duplicate cached-read fetchers, phantom cache identities, and any helper whose only purpose was forwarding
one of those shapes. Do not leave compatibility aliases.

Update `docs/frontend.md` and the synchronized system description in `docs/systems.md` / `docs/systems.html`
in plain language: the cache engine manages loaded values, the Resource Catalogue names cached server reads
and their dependencies, server events invalidate through that catalogue, and successful writes apply one
named immediate effect through the same catalogue. Rebuild checked-in `web/dist` because FastAPI serves it.

## Explicit exclusions

- No backend, database, API route, response, event-kind, event-payload, auth, or WebSocket protocol change.
- No optimistic canonical frontend state, entity store, reactive-query runtime, service worker, persistence,
  offline queue, retry policy, polling redesign, or whole-screen refetch.
- No UI, copy, CSS, route, navigation, loading, error, timing, animation, or accessibility redesign.
- No mutation, upload, managed-file, copy-text, Chat delivery, or `/api/meta` catalogue entry.
- No Worker-type default, Ticket ingress, Stage, Review-shape, Chat-turn, Managed Markdown, or Employee-history
  change from other architecture tickets.
- No compatibility aliases for old free-form resource or mutation-invalidation shapes.

## Plan requirements

The delegated plan must inventory every production/test cached read, key, endpoint, response type, generic
cache operation, event dependency, explicit refresh, and UI mutation. It must freeze exact TypeScript
declarations for catalogue entries and named mutation effects; explain entity-first matching, today's-Day
context, Review ordering, Chat refresh policy, cache-engine boundary, deletion set, RED order, preservation
commands, documentation/build work, and a bounded changed-path allowlist. Any need for a backend/event/UI
contract change returns to the orchestrator before implementation.
