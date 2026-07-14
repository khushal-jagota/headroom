# AD08 implementation plan — Deep frontend Resource Catalogue

## 1. Fixed outcome and seam

Create one deep frontend Resource Catalogue at `web/src/lib/resourceCatalogue.ts`. Its interface gives a
caller one typed way to open each cached server projection and one closed, typed way to name the resource
effect of a successful UI mutation. The same catalogue definitions are the only source used to derive event
invalidation identities.

Keep `web/src/lib/resources.svelte.ts` as the internal generic cache engine. It continues to own reactive
state, shared entries, fetch de-duplication, force refresh, stale-response suppression, last-good-data
retention, subscriber counts, and disposal. No route or component imports that engine after this ticket.

The server remains the canonical source of truth. The two refresh signals stay separate:

1. typed server events map through catalogue dependencies and trigger targeted refetches; and
2. a successful UI mutation immediately applies its named catalogue effect without waiting for that event.

This ticket does not add optimistic state, a client entity store, a reactive-query runtime, a polling
redesign, a backend/event/HTTP change, or any visible UI change. It also does not create a Worker type or
Ticket. There is no live Worker-type default anywhere in this plan; `workerTypeManifests()` is only the
existing typed cached read of `/api/worker-types`. The existing historical migration rewrite may classify
old stored rows as `coding`; that rewrite is the only coding-classification concession. AD08 adds no live
inference, fallback, or default to `coding` or any other Worker type.

## 2. Complete current inventory

### Generic cache operations

`resources.svelte.ts` currently exports:

- `resource(key, fetcher)`, which creates or subscribes to one shared keyed entry and starts a read only when
  the entry is stale or has never loaded;
- `ResourceHandle.data/error/loading/stale`, `refresh()`, `invalidate()`, and idempotent `dispose()`;
- `peek(key)`, used only by `ws.ts` to read the cached `day:today` value without subscribing;
- `invalidate(key)` and `invalidateMany(keys)`, used by WebSocket batches and mutation effects;
- `refresh(key)`, currently unused outside the engine but required internally for the ordered Review effect;
- `mutateJson(path, options, expectedInvalidations)`, which performs an ordinary HTTP write and then accepts
  a free-form string array; and
- `__resourceStats()`, a diagnostic/test view of entries.

Preserve these engine behaviors exactly:

- concurrent non-forced reads of one key share one promise;
- a forced refresh aborts/supersedes the older request and only the newest version may update state;
- a refresh error retains last-good data and marks it stale; a first-load error retains no data;
- invalidating an entry with subscribers starts one forced read; invalidating an entry without subscribers
  only marks it stale; invalidating a key not yet in the cache performs no read;
- a later subscriber to a stale entry starts the read;
- duplicate identities in one batch are invalidated once;
- disposal decrements the subscriber count once but does not delete cached data or abort a shared read; and
- the original structured error object reaches the handle unchanged.

Delete the unused public `ResourceHandle.invalidate()` escape hatch. Make single-key `invalidate` private.
Keep `resource`, `peek`, `invalidateMany`, and keyed `refresh` exported only so the catalogue and focused
engine test can use them. The catalogue becomes the sole production caller of `peek`: once for today's Day
context and, on Project-name events, for the Ticket instances opened through the catalogue. `peek` remains a
semantic-free current-data lookup. Delete `mutateJson` from this engine; mutation HTTP plus effects move to
the catalogue interface below. Keep `__resourceStats()` for its existing diagnostic role.

### Every cached projection, identity, endpoint, type, caller, and current policy

The production catalogue contains exactly these thirteen entries:

| Catalogue method | Stable identity | GET path | Response type | Current callers | Refresh policy |
| --- | --- | --- | --- | --- | --- |
| `board()` | `board` | `/api/board` | `BoardResponse` | `BoardRoute` | ordinary Board events + Project-name events + mutation effects + manual handle refresh |
| `review()` | `review` | `/api/review` | `ReviewResponse` | `App` badge/presence and `ReviewRoute` | narrow events + mutation effects + stale-self-heal/manual refresh |
| `todayDay()` | `day:today` | `/api/day/today` | `DayResponse` | `DayRoute` | current-day events + Project-name events + mutation effect + manual refresh |
| `backlogSprintItems()` | `items:backlog` | `/api/items?sprint_id=null` | `BacklogResponse` | `BacklogRoute` | Sprint-item events + Project-name events + mutation effects + manual refresh |
| `ideas()` | `ideas` | `/api/ideas` | `IdeasResponse` | `IdeasRoute` | Idea events + Project-name events + mutation effect + manual refresh |
| `projects()` | `projects` | `/api/projects` | `ProjectsResponse` | `BacklogRoute`, `IdeasRoute`, `TicketRoute` | every Project event + manual refresh |
| `sprintSummaries()` | `sprints` | `/api/sprints` | `SprintsResponse` | `TicketRoute` | Sprint events + mutation effect + manual refresh |
| `currentSprint()` | `sprint:current` | `/api/sprint/current` | `CurrentSprintResponse` | `SprintRoute`, `TicketRoute` | Ticket/Sprint-item/Sprint events + Project-name events + mutation effects + manual refresh |
| `ticket(ticketId)` | `ticket:<ticketId>` | `/api/tickets/<encoded-ticketId>` | `TicketDetail` | `TicketRoute`, current decision in `ReviewRoute` | matching Ticket/cross-entity events, Ticket `chat_session_created`, matching Project-name events for opened instances + mutation effects + manual refresh |
| `panelsChat(entityId)` | `chat:<entityId>` | `/api/chat/<encoded-entityId>/state` | `ChatStateResponse` | every `ChatPanel` | all five matching Chat events + manual/poll/action refresh + refresh cached data on remount |
| `chatGatewayStatus(entityId)` | `chat-status:<entityId>` | `/api/chat/<encoded-entityId>/status` | `GatewayStatus` | `TicketRoute`, `BoardRoute`, `ChiefOfStaffRoute` | initial cached load/manual only; no event dependency |
| `chatCommands()` | `chat-commands` | `/api/chat/commands` | `CommandCatalog` | every `ChatPanel` | initial cached load/manual only; no event dependency |
| `workerTypeManifests()` | `worker-types` | `/api/worker-types` | `WorkerTypesResponse` | `BoardRoute`, `TicketRoute`, `ReviewRoute` | initial cached load/manual only; static per deploy |

All handles retain the existing explicit `dispose()` at component destruction. Equal identities continue to
share one engine entry even when opened by several callers: in particular `App` and `ReviewRoute` share
Review, all project consumers share Projects, Ticket and Sprint screens share current Sprint, every
manifest consumer shares Worker manifests, and a Review decision shares its Ticket entry with any mounted
Ticket view.

There are no catalogue entries for `item:<id>`, `sprint:<id>`, or `day:<date>`, because production has no
cached read for them. There is no `queues` identity. The mutation/upload/file/copy/startup requests listed
below are not catalogue entries.

### Every ordinary request outside catalogue scope

- `App` keeps startup `GET /api/meta` through `fetchJson`; it supplies WebSocket debounce configuration.
- Every route keeps its POST/PATCH/PUT endpoint and body as an ordinary HTTP mutation passed to the typed
  mutation helper; the catalogue does not register write endpoints or payloads.
- `TicketRoute` copy text remains `fetchText` plus clipboard handling.
- `ChatPanel` start-turn, image upload, and Pause stay the ordinary functions in `api.ts`.
- `FilePreview` and the full preview route keep ordinary managed-file/HTML/media reads.
- Clipboard operations and all direct browser media loads remain outside the catalogue.

### Every current explicit refresh

- `ReviewRoute` refreshes Review once when the selected decision/detail proves stale. Its
  `staleRefreshRequests` set keeps this once per decision key.
- Review accept and return-for-revision currently wrap `mutateJson` in `refreshReviewAfter`; this wrapper is
  deleted and its exact ordering moves into the two named effects below.
- `ChatPanel` force-refreshes a cached, non-stale Chat state when remounted. This is entry-specific refresh
  policy and moves behind `panelsChat(entityId)`.
- `ChatPanel` refreshes Chat state every 500 ms while an active turn exists, once after a successful turn
  start, and once after successful Pause. These remain explicit `ResourceHandle.refresh()` calls because
  their timing and error handling belong to Chat interaction, not generic cache policy.
- No other production caller invokes `.refresh()`.

## 3. Freeze the public TypeScript interface

`web/src/lib/resourceCatalogue.ts` exports exactly these types and operations:

```ts
import type { FetchOptions } from "./api";
import type { WorkerTypesResponse } from "./lifecycle";
import type { ResourceHandle } from "./resources.svelte";
import type {
  BacklogResponse,
  BoardResponse,
  ChatStateResponse,
  CommandCatalog,
  CurrentSprintResponse,
  DayResponse,
  GatewayStatus,
  IdeasResponse,
  ProjectsResponse,
  ReviewResponse,
  SprintsResponse,
  TicketDetail
} from "./types";

export type EventEntityPrefix =
  | "t"
  | "si"
  | "sp"
  | "day"
  | "idea"
  | "project"
  | "agent";

export type CatalogueResourceIdentity =
  | "board"
  | "review"
  | "day:today"
  | "items:backlog"
  | "ideas"
  | "projects"
  | "sprints"
  | "sprint:current"
  | "chat-commands"
  | "worker-types"
  | `ticket:${string}`
  | `chat:${string}`
  | `chat-status:${string}`;

export type PlannerEvent = Readonly<{
  id: number;
  entity_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: number;
}>;

export type ResourceEventContext = Readonly<{
  todayDayId: string | null;
}>;

export type ResourceMutationEffect =
  | Readonly<{ kind: "ideaCreated" }>
  | Readonly<{ kind: "backlogSprintItemCreated" }>
  | Readonly<{ kind: "ticketChanged"; ticketId: string }>
  | Readonly<{ kind: "ticketTitleChanged"; ticketId: string }>
  | Readonly<{ kind: "ticketReviewStateChanged"; ticketId: string }>
  | Readonly<{ kind: "reviewTicketAccepted"; ticketId: string }>
  | Readonly<{ kind: "reviewTicketReturnedForRevision"; ticketId: string }>
  | Readonly<{ kind: "todayDayChanged" }>
  | Readonly<{ kind: "currentSprintChanged" }>;

export interface ResourceCatalogue {
  board(): ResourceHandle<BoardResponse>;
  review(): ResourceHandle<ReviewResponse>;
  todayDay(): ResourceHandle<DayResponse>;
  backlogSprintItems(): ResourceHandle<BacklogResponse>;
  ideas(): ResourceHandle<IdeasResponse>;
  projects(): ResourceHandle<ProjectsResponse>;
  sprintSummaries(): ResourceHandle<SprintsResponse>;
  currentSprint(): ResourceHandle<CurrentSprintResponse>;
  ticket(ticketId: string): ResourceHandle<TicketDetail>;
  panelsChat(entityId: string): ResourceHandle<ChatStateResponse>;
  chatGatewayStatus(entityId: string): ResourceHandle<GatewayStatus>;
  chatCommands(): ResourceHandle<CommandCatalog>;
  workerTypeManifests(): ResourceHandle<WorkerTypesResponse>;
}

export const resourceCatalogue: ResourceCatalogue;

export function mutateJsonWithResourceEffect<T = unknown>(
  path: string,
  options: FetchOptions,
  effect: ResourceMutationEffect
): Promise<T>;

export function keysForEvent(
  event: PlannerEvent,
  context?: ResourceEventContext
): CatalogueResourceIdentity[];

export function invalidateCatalogueResources(
  identities: readonly CatalogueResourceIdentity[],
  reason: string
): void;

export function knownEntityPrefixes(): readonly EventEntityPrefix[];

export type { ResourceHandle } from "./resources.svelte";
```

The response types in the interface are the existing imports from `types.ts` and `lifecycle.ts`; do not
redeclare them. `PlannerEvent` moves from `ws.ts` to this interface because it is the event-dependency input;
`ws.ts` imports the type. There is no separate `.d.ts` copy.

Normalize every Ticket/Chat parameter once inside the catalogue: require a non-empty string, preserve its
exact value for identity, and use `encodeURIComponent` only for the path segment. Do not trim, lowercase,
default, or reinterpret an id. Mutation effects call the same normalizer before deriving identities. This
keeps a parameterized key and endpoint inseparable without inventing branded ids or new validation rules.

Unknown effect kinds and missing `ticketId` values are TypeScript errors through the closed discriminated
union. Implement effect selection with an exhaustive `never` check, not a default key list. Callers never
pass a `CatalogueResourceIdentity` or raw invalidation array to a mutation.

## 4. One catalogue definition and dependency model

Inside `resourceCatalogue.ts`, define the thirteen entries once in a single private
`RESOURCE_DEFINITIONS` object. Each definition owns:

- the identity constructor;
- an identity recognizer (exact equality for static entries; non-empty suffix for parameterized entries);
- the exact GET path constructor;
- its response type through the typed definition/open method;
- for a parameterized entry, registration of each definition-built identity opened through the common
  catalogue opener;
- `eventInvalidated: true | false`;
- `refreshCachedOnOpen: true | false`; and
- its affected-by-event function when event-invalidated.

The public `resourceCatalogue` methods open handles from these definitions. `keysForEvent` iterates those
same affected-by-event functions. Mutation effects resolve identities through those definitions. Runtime
identity recognition in `keysForEvent` and `invalidateCatalogueResources` is derived from those definitions,
including the three parameterized identity constructors. Do not maintain a second list of valid resource
strings, a second route-to-key map, or a switch in `ws.ts`.

The catalogue also owns one private `OPENED_PARAMETERIZED_IDENTITIES` process-lifetime set containing
parameterized identities actually opened by its methods. The common catalogue opener inserts the identity
returned by a parameterized definition into this set; mapper output, invalidation input, route strings, and
static identities never populate it. The index therefore reuses definition-built identities rather than
becoming a second hand-authored vocabulary. It is not exported or reset, and disposal does not remove
entries: the generic cache and the opened-instance index intentionally share process lifetime. Only the
Ticket definition consults the index in AD08; Chat and Chat-status instances are ignored by Project
matching. The generic cache engine stores values and exposes `peek`, but contains no Project, Ticket, or
event knowledge.

Use these exact policies:

- `refreshCachedOnOpen` is `true` only for `panelsChat`; after opening its handle, force refresh only when
  the shared entry already has data and is not stale. New/stale Chat state already fetches in the cache
  constructor and must not receive a duplicate force read.
- It is `false` for all other entries, which reuse fresh cached data on another subscription.
- The first ten entries through `panelsChat` are event-invalidated according to the dependency rules below.
- Chat gateway status, Chat commands, and Worker manifests have no event dependency. They still expose the
  common manual `refresh()` handle method but no production timer currently calls it.

This object is the one Resource Catalogue. Do not add a parallel `eventMapping` module or a shallow
manifest/resource forwarding helper.

`keysForEvent` follows one order: validate the entity prefix; choose the exact Ticket Chat set below when it
matches, otherwise choose the ordinary primary-prefix set; apply the Project-name, narrow Review,
link/blocked, and Day rules; de-duplicate; then validate every resulting identity against the catalogue. The
Ticket Chat branch is never merged with the ordinary Ticket set. A Project-name event discovers Ticket
instances only through the catalogue-owned opened-instance index; it never scans routes or constructs a
Ticket identity from outside that index.

## 5. Exact entity-first event dependency rules

### Primary prefix behavior

Validate `event.entity_id` has one of the seven prefixes above. An unknown/malformed prefix throws; `ws.ts`
keeps its current catch-and-debug behavior for that event.

For an ordinary, previously unseen event kind, the primary prefix rules are:

| Prefix | Real catalogue identities |
| --- | --- |
| `t` | matching Ticket, Board, current Sprint |
| `si` | backlog Sprint items, Board, current Sprint |
| `sp` | Sprint summaries, current Sprint |
| `day` | today's Day only when the id is today or today is not cached yet |
| `idea` | Ideas |
| `project` | Projects |
| `agent` | matching Panels Chat |

This is not a per-kind allowlist. A synthetic new kind on every known prefix reaches those real projections
without editing a kind table. Only the explicit exceptions below use kind/payload logic. Resolve the Chat
exceptions before applying the ordinary Ticket prefix set, so no Chat kind accidentally reaches Board or
current Sprint.

No primary rule returns an individual Sprint item, individual Sprint, or dated Day identity. Board remains
the backend projection name. No event returns Chat gateway status, commands, or manifests.

### Chat and shared Ticket-session exceptions

The exact Chat kinds remain:

```text
chat_session_created
chat_message_recorded
chat_turn_started
chat_turn_updated
chat_turn_finished
```

For a Ticket (`t_`) entity, use this exact projection table instead of the ordinary Ticket prefix set:

| Event kind | Exact identities |
| --- | --- |
| `chat_session_created` | matching Ticket and matching Panels Chat |
| `chat_message_recorded` | matching Panels Chat only |
| `chat_turn_started` | matching Panels Chat only |
| `chat_turn_updated` | matching Panels Chat only |
| `chat_turn_finished` | matching Panels Chat only |

`chat_session_created` is temporarily shared until AD09: Employee claim, human-turn binding, and legacy
history remint can all persist the chattable session key on the Ticket row and emit this same event kind.
`TicketDetail` exposes that stored key, while Panels Chat resolves its state from the same row. Therefore the
current projection union is exactly the matching Ticket plus matching Panels Chat, with no Board, current
Sprint, or Review.

An Agent (`agent_`) entity always maps only to its matching Panels Chat entry because Panels Chat is that
prefix's sole cached projection. Never invalidate one Chat entity for another entity's event. No other
Ticket event invalidates Panels Chat. Chat status remains unaffected.

### Project-name read-model exception

Ordinary Project primary behavior remains Projects only. Preserve these exact cases:

| Project event | Exact identities before opened-Ticket matching |
| --- | --- |
| `project_created` | Projects only |
| `project_updated` with `payload.fields` absent or not containing `name` (including summary-only) | Projects only |
| `project_updated` with `payload.fields` containing `name` (including mixed name/summary updates) | Projects, Board, today's Day, backlog Sprint items, Ideas, current Sprint |
| any synthetic ordinary Project kind | Projects only |

The six identities in the name row are the complete aggregate set whose current server projections embed
Project names. The corresponding six `RESOURCE_DEFINITIONS` own that reachability: Projects keeps its
ordinary Project-prefix rule, while Board, today's Day, backlog Sprint items, Ideas, and current Sprint add
the exact `project_updated`/`Array.isArray(payload.fields) && payload.fields.includes("name")` predicate. Do
not add Review, Panels Chat, Sprint
summaries, Chat status, Chat commands, or Worker manifests.

For the same Project-name event, the Ticket definition additionally filters
`OPENED_PARAMETERIZED_IDENTITIES` through its own identity recognizer. For every opened `ticket:<id>`:

- call the generic engine's `peek<TicketDetail>(identity)`;
- include the Ticket when `peek` returns `undefined`, which conservatively covers an entry with no loaded
  data and a first load still in progress;
- include it when loaded data has `project_id === event.entity_id`; and
- exclude it when loaded data proves its direct `project_id` is another Project or `null`/absent.

If a refresh is in progress but last-good Ticket data exists, `peek` returns that data and the known
`project_id` rule applies. This adds no server read: invalidation refreshes only subscribed cache entries and
marks an opened-but-unsubscribed entry stale. Project creation and summary-only updates never consult this
Ticket matching path. No raw or merely recognized `ticket:<id>` counts as opened; only a call to
`resourceCatalogue.ticket(id)` can put its definition-built identity in the index.

### Review's narrow exception

Add Review only for these Ticket event kinds:

```text
stage_changed
proposal_accepted
proposal_superseded
proposal_filed
kickoff_proposal_filed
kickoff_accepted
approval_returned
ticket_status_changed
ticket_deleted
```

Also add Review for `ticket_updated` only when `payload.field === "title"`. Do not add it for Ticket
creation itself, any Chat event (including the shared Ticket session event), notes, recap, value, scope,
links, blocker augmentation, non-title metadata, Sprint items, or unrelated Tickets merely because the
Review screen is open. Ticket creation's existing proposal/status companion events remain the conservative
Review signal.

### Link and affected-blocked endpoints

For `link_added` and `link_removed`, apply the real primary projection dependencies of both non-empty
`payload.from_id` and `payload.to_id`. For `payload.affected_blocked_target_ids`, apply those dependencies to
each target id. The backend contract permits Ticket and Sprint-item endpoints:

- a Ticket endpoint means its named Ticket plus Board and current Sprint;
- a Sprint-item endpoint means backlog Sprint items plus Board and current Sprint.

This replaces the inert `item:<id>` output with the Sprint item's real aggregates. It does not add Review;
AD04 explicitly excludes links and blocker augmentation. Unknown endpoint prefixes continue to fail the
whole event mapping and be logged by `ws.ts`, rather than producing a guessed key.

### Day membership and cold start

For `day_ticket_added` and `day_ticket_removed` with `payload.ticket_id`, always add that named Ticket and
Board. Add today's Day and Review only when:

- `context.todayDayId === event.entity_id`; or
- `context.todayDayId === null`, the conservative cold-start case.

A known off-day membership event therefore updates its Ticket/Board dependencies but not today's Day or
Review. Other Day events affect today's Day only under the same today/cold rule.

If `keysForEvent` receives an explicit context, use it. If omitted in production, it calls internal
`peek<DayResponse>("day:today")` exactly once for that event and derives `todayDayId` from the cached data or
`null`. Delete `includeTodayAlias`; cold start is represented by the one nullable fact. `ws.ts` calls
`keysForEvent(plannerEvent)` once per event and does not peek itself.

### WebSocket batching preservation

`ws.ts` retains cursor updates, `since=<cursor>`, one socket, 250 ms/default configured debounce, pending
identity `Set`, reset-on-flush, debug counters, close/reconnect backoff from 500 ms to 10 seconds, malformed
JSON handling, unknown-event logging, and stop cleanup. Its pending set becomes
`Set<CatalogueResourceIdentity>`. A flush calls `invalidateCatalogueResources` once with the de-duplicated
identities. No whole-screen or all-catalogue invalidation is added.

Before returning or invalidating, validate each identity against `RESOURCE_DEFINITIONS`. Thus a mapper bug
cannot silently emit an identity the catalogue cannot open.

## 6. Exact successful-mutation effects and ordering

`mutateJsonWithResourceEffect` owns only the post-success resource effect. It keeps the route-supplied path,
method, body, headers, and result type as an ordinary `fetchJson` request.

Its ordering is fixed:

1. Await `fetchJson`.
2. If it fails, propagate the same error and perform no invalidation or refresh.
3. Resolve the closed effect through `RESOURCE_DEFINITIONS` and immediately invalidate its de-duplicated
   ordinary identities. This synchronously marks entries stale and starts reads only for subscribed entries,
   exactly as the current engine does.
4. For the two Review advance effects only, do not put Review in that ordinary invalidation set. Invoke the
   cache engine's keyed `refresh("review")` exactly once, await it if the entry exists, swallow that refresh
   error as `refreshReviewAfter` does today, then return the mutation result.
5. All other effects return the mutation result after scheduling their immediate targeted reads, matching
   current `mutateJson` behavior.

Freeze this effect table:

| Effect | Callers/mutations | Immediate identities | Ordered awaited work |
| --- | --- | --- | --- |
| `ideaCreated` | Ideas capture POST | Ideas | none |
| `backlogSprintItemCreated` | Backlog item POST | backlog Sprint items, current Sprint, Board | none |
| `ticketChanged(ticketId)` | Ticket non-title PATCH, scope, note, field value, recap | named Ticket, Board, current Sprint | none |
| `ticketTitleChanged(ticketId)` | Ticket title PATCH from Ticket or Review | named Ticket, Board, current Sprint, Review | none |
| `ticketReviewStateChanged(ticketId)` | Ticket-route accept and takeover/release | named Ticket, Board, current Sprint, Review | none |
| `reviewTicketAccepted(ticketId)` | Review accept | named Ticket, Board, current Sprint | exactly one swallowed-error Review refresh |
| `reviewTicketReturnedForRevision(ticketId)` | Review send-back | named Ticket, matching Panels Chat, Board, current Sprint | exactly one swallowed-error Review refresh |
| `todayDayChanged` | today's Day field PATCH | today's Day | none |
| `currentSprintChanged` | current Sprint document/name field PATCH | current Sprint, Sprint summaries | none |

There is intentionally no dated-Day identity in `todayDayChanged` and no individual-Sprint identity in
`currentSprintChanged`. Review title save uses `ticketTitleChanged`; it does not need a route-specific alias.
Ticket accept and takeover/release share one effect because their exact affected projection set is the
same. Do not add effects for direct CLI/server mutations; their canonical signal remains the event stream.

### Route rewiring inventory

- `IdeasRoute`: `resourceCatalogue.ideas()`, `.projects()`, and `ideaCreated`.
- `BacklogRoute`: `.backlogSprintItems()`, `.projects()`, and `backlogSprintItemCreated`.
- `DayRoute`: `.todayDay()` and `todayDayChanged`; it still derives the PATCH date from loaded Day id.
- `SprintRoute`: `.currentSprint()` and `currentSprintChanged`.
- `TicketRoute`: `.ticket(id)`, `.sprintSummaries()`, `.projects()`, `.chatGatewayStatus(id)`,
  `.currentSprint()`, `.workerTypeManifests()`, and the three Ticket effects above. Delete
  `baseTicketInvalidations` and `reviewTicketInvalidations`.
- `ReviewRoute`: `.review()`, `.ticket(decision.ticket_id)`, `.workerTypeManifests()`,
  `reviewTicketAccepted`, `ticketTitleChanged`, and `reviewTicketReturnedForRevision`. Delete
  `refreshReviewAfter`; retain only stale-self-heal's explicit `review.refresh()`.
- `BoardRoute`: `.board()`, `.chatGatewayStatus(chief id)`, `.workerTypeManifests()`.
- `ChiefOfStaffRoute`: `.chatGatewayStatus(chief id)`.
- `App`: `.review()`; `/api/meta` remains ordinary.
- `ChatPanel`: `.chatCommands()` and `.panelsChat(stableEntityId)`.

Every route keeps current loading/error/data usage and its exact disposal calls. No markup, props, copy,
keyboard behavior, loading state, or error handling changes.

## 7. Chat refresh policy stays distinct

The Panels Chat resource is cached server state, but Chat writes do not become catalogue entries:

- image uploads remain ordinary and cause no cache effect by themselves;
- a successful start-turn remains followed by one awaited `chatState.refresh()`; a refresh error still
  displays an error while submit returns success as today;
- active-turn polling remains the same 500 ms one-shot scheduling around `activeTurn` and loading state;
- successful Pause remains followed by one awaited `chatState.refresh()` in the same `try`; and
- remount refresh of already-fresh cached Chat state moves behind the `panelsChat` entry's
  `refreshCachedOnOpen` policy.

All five matching Chat event kinds invalidate a Ticket's Panels Chat. The four message/turn kinds invalidate
only that Chat entry. Ticket `chat_session_created` invalidates exactly the matching Ticket and matching Chat
because, until AD09, both projections expose state derived from the session key stored on the Ticket row. It
does not invalidate Board, current Sprint, or Review. Ordinary non-Chat Ticket events do not refresh Chat.
This is the Ticket's current binding policy and corrects the stale doc statement that every Ticket event
reloads a Hermes trace without pretending the Employee/Hermes history seam is already separated.

Chat gateway status, command catalogue, and Worker manifests retain cached initial loading and error
behavior but receive no event invalidation. Do not invent availability/config events.

## 8. Deletion set

Delete rather than alias:

- `web/src/lib/eventMapping.mjs` and `eventMapping.d.ts`; event matching lives in the catalogue;
- `web/src/lib/resources.ts`, the one-line generic cache barrel;
- `web/src/lib/manifest.svelte.ts`, the one-resource forwarding helper;
- `web/tests/event-mapping.test.mjs`, replaced at the catalogue interface;
- `resources.svelte.ts`'s free-form `mutateJson(..., expectedInvalidations)` and
  `ResourceHandle.invalidate()` surface;
- route-local resource keys, cached GET fetchers, invalidation arrays, Ticket invalidation constants, and
  `refreshReviewAfter`;
- `ws.ts`'s raw `peek`, raw `invalidateMany`, and duplicate event type; and
- every production/test expectation for `item:<id>`, `sprint:<id>`, `day:<date>`, `includeTodayAlias`, or a
  route-owned expected-invalidation array.

Do not retain `manifestResource`, re-export old generic names, create an `eventMapping` compatibility file,
or keep phantom identities solely for an old test.

## 9. RED-first test plan

Write the new interface tests before production rewiring. Do not weaken an existing browser assertion.

### RED 1 — catalogue ownership and typed read contract

Add `web/tests/resource-catalogue.test.mjs`. Use the existing dev `typescript` package or the repository's
small source-transform pattern to load `resourceCatalogue.ts` with fake cache/API imports; add no package.
The test exercises the public interface and scans production source only where ownership must be static.

First prove RED for:

- exactly the thirteen public methods, identities, paths, and response-type declarations in section 2;
- parameterized Ticket/Chat key sharing and URL encoding with no value normalization/default;
- only `resourceCatalogue.ts` importing/calling the generic `resource` factory in production;
- no route/component/manifest/WebSocket file constructing a cached key, GET fetcher, or raw resource
  invalidation array;
- App/Review, repeated Projects/current Sprint/status/manifest/Chat consumers receiving the same shared
  engine key and one canonical endpoint;
- the private opened-parameterized-instance index being populated only by catalogue method opens, retaining
  definition-built identities for the same process lifetime as cache entries, and never accepting route or
  mapper strings;
- deleted barrel, manifest forwarder, event mapper, old mutation signature, and compatibility aliases; and
- startup meta, Chat operations/uploads, copy text, and file reads remaining outside the catalogue.

Use `npm --prefix web run check` to prove the exact declarations and all production callers. The Node suite
also asks the existing TypeScript compiler to type-check a temporary negative fixture: an unknown effect
kind and each missing required `ticketId` must fail, while every declared effect compiles. Do not add a
tracked type-test fixture. The Node test proves observable definitions with fakes rather than duplicating
production key/path logic.

### RED 2 — mutation-effect behavior

Through fake `fetchJson`, invalidation, and keyed refresh adapters, cover all nine union members and assert
the exact table in section 6:

- callers supply effects, never identities;
- duplicate identities are removed;
- a failed mutation performs zero invalidations and zero refreshes;
- ordinary success invalidates immediately and returns the HTTP result;
- Review accept and return each exclude Review from ordinary invalidation, call keyed Review refresh once,
  await it, swallow only that refresh failure, and return the mutation result; and
- Review return includes only the matching Panels Chat while Review accept does not.

Replace the route-source array assertions at the bottom of
`tests/unit/test_review_ticket_decisions_type_driven.py` with the named-effect contract. Keep all AD04 HTTP,
response, old-Queue absence, Ticket-only rendering, and data-attribute assertions unchanged.

### RED 3 — event dependencies and anti-rot

Fold the current `event-mapping.test.mjs` behavior into `resource-catalogue.test.mjs`, then update
`tests/unit/test_frontend_event_mapping.py` to run the new suite with the real backend `EventKind` values.

Prove:

- every backend `EventKind` returns at least one valid real catalogue identity under its representative
  known prefix/context;
- every prefix accepts a synthetic ordinary kind without a kind-list edit;
- malformed/unknown event prefixes and unknown link/blocked endpoint prefixes throw;
- every identity from `keysForEvent` is accepted by `invalidateCatalogueResources`, while a fabricated
  identity is rejected;
- every event-invalidated catalogue entry has at least one fixture that reaches it; the three manual-only
  entries are explicitly excluded and never reached;
- no mapping emits `queues`, `item:<id>`, individual `sprint:<id>`, or dated `day:<date>`;
- Ticket, Sprint-item, Sprint, Idea, Project, and Agent primary sets are exact;
- `project_created`, a summary-only `project_updated`, and a synthetic ordinary Project event produce only
  Projects;
- a `project_updated` whose `payload.fields` contains `name`, alone or mixed with `summary`, produces exactly
  Projects, Board, today's Day, backlog Sprint items, Ideas, and current Sprint before Ticket instances;
- after opening Ticket handles through the catalogue, that Project-name event also includes Tickets with a
  matching loaded direct `project_id` and Tickets whose first load is still unresolved/has no peeked data,
  while excluding loaded Tickets whose direct Project is unrelated, null, or absent;
- raw invalidation/mapper identities do not register an opened Ticket, disposed opened Tickets remain
  indexed with the cache, and summary-only/creation events never fan out to opened Tickets;
- Project-name events never reach Review, Chat, Sprint summaries, Chat status, Chat commands, or Worker
  manifests;
- the four Ticket message/turn Chat events produce only matching Chat;
- Ticket `chat_session_created` produces exactly matching Ticket plus matching Chat, with no Board, current
  Sprint, or Review, regardless of which current session-binding path emitted it;
- all five Agent Chat events produce only matching Chat;
- Review's exact kinds/title rule and all exclusions stay exact;
- link and affected-blocked endpoints reach named Tickets or real Sprint-item aggregates;
- today's Day/membership known-today, known-off-day, and cold-start sets are exact; and
- production `ws.ts` calls `keysForEvent` once per event with no direct peek or second identity vocabulary.

Do not add a ceremonial frontend `EventKind` table. The Python wrapper supplies the canonical backend list;
the entity-first/synthetic tests prove new ordinary kinds require no mapper edit.

### RED 4 — generic cache-engine preservation

Add `web/tests/resource-cache.test.mjs`, loading `resources.svelte.ts` with `$state` and side-effect imports
replaced by deterministic fakes. Test through `resource`, handle state, `invalidateMany`, and keyed `refresh`:

- same-key concurrent de-duplication;
- forced refresh abort/supersession and ignored stale completion;
- last-good-data/error/stale behavior;
- exact structured-error propagation;
- subscriber invalidation refetch versus unsubscribed stale marking;
- a later subscriber refetching stale data;
- idempotent disposal without entry deletion;
- batch de-duplication;
- `peek` returning current data without subscribing and `undefined` for a missing/first-load entry, with no
  Project or Ticket semantics in the engine; and
- missing-key invalidation/refresh performing no request.

Use unique test keys rather than adding a production cache-reset interface.

### RED 5 — browser two-signal and ordering proof

Add `tests/e2e/test_resource_catalogue.py` with request counting and a Playwright-routed/inert event socket;
do not add a production test hook. Prove:

- an Ideas or Backlog UI mutation visibly reloads its projection with the socket absent, while an unrelated
  loaded projection is not fetched;
- failed UI mutation leaves all loaded projections untouched;
- Review accept and return-for-revision each cause exactly one post-success `/api/review` GET with the socket
  absent, after their immediate named Ticket/Chat/aggregate effect, and the UI advances;
- App and Review share the one Review entry rather than issuing one read per subscriber;
- with the socket active, exercise Board, Day, Backlog, Ideas, Sprint, and Ticket routes so a Project-name
  event refetches only the subscribed members of the six-aggregate set: Board; today's Day; backlog plus
  Projects; Ideas plus Projects; current Sprint; and, on a matching Ticket route, Projects plus current
  Sprint plus that Ticket respectively;
- on the Ticket route, a summary-only Project update refetches Projects only, and a Project-name event for
  another Project does not refetch the loaded Ticket;
- the Project-name network proof observes no Review, Sprint-summary, Chat, Chat-status, command, or manifest
  request;
- with the socket active, Ticket `chat_session_created` refetches only the subscribed matching Ticket and
  matching Chat, never Board, current Sprint, or Review;
- with the socket active, each of the other four matching Ticket Chat events refetches only matching Chat;
  and
- a later unrelated event does not fetch the observed projection.

The unit catalogue test owns exact identity sets; browser assertions own network ordering, visible refresh,
and absence of whole-screen refetch.

## 10. Existing preservation suites and focused commands

Keep these existing tests unchanged except the two explicit static-test migrations above:

- `tests/e2e/test_flows_a.py`: multi-context live Board, current-day Review/badge, Ticket proposal/title/
  accept/takeover paths, Review accept/send-back, Chat start/commands, and no-reload transitions.
- `tests/e2e/test_flows_b.py`: today's Day initial/edit/focus behavior, Review-to-done, refresh restore, current
  Sprint live Ticket/item regrouping, and Sprint document edits.
- `tests/e2e/test_backlog_ideas.py`: successful capture reaches Backlog/Ideas without optimistic canonical
  state and preserves Projects selectors.
- `tests/e2e/test_live_chat_state.py` and `test_chat_images.py`: initial/remounted Chat state, active polling,
  streaming/activity stability, Pause, navigation, image/start failures, matching live updates, and the
  existing shared Ticket session-key behavior.
- `tests/e2e/test_chief_of_staff.py`: Chief route and Workspace's shared Chat status/state consumers.
- `tests/e2e/test_board_stage_indicators.py`: Board plus Worker-manifest sharing and Project grouping.
- `tests/e2e/test_ticket_file_previews.py`: AD07 Managed Markdown subtree identity through Ticket/Chat resource
  updates; no AD07 file is an AD08 edit target.
- `tests/e2e/test_trusted_ingress_browser.py`: WebSocket origin behavior while `ws.ts` is rewired.
- `web/tests/lifecycle.test.mjs`: pure manifest interpretation remains outside the catalogue.
- backend endpoint/event/domain tests remain unchanged; AD08 changes no server contract.

Focused implementation commands, before the final gate, are:

```text
npm --prefix web run check
npm --prefix web test
.venv/bin/pytest tests/unit/test_frontend_event_mapping.py tests/unit/test_review_ticket_decisions_type_driven.py
.venv/bin/pytest tests/e2e/test_resource_catalogue.py tests/e2e/test_flows_a.py tests/e2e/test_flows_b.py tests/e2e/test_backlog_ideas.py tests/e2e/test_live_chat_state.py tests/e2e/test_chat_images.py tests/e2e/test_chief_of_staff.py tests/e2e/test_board_stage_indicators.py tests/e2e/test_ticket_file_previews.py tests/e2e/test_trusted_ingress_browser.py
```

Build checked-in assets once after source/tests/docs are green. Then run one complete `./verify` and retain
its full output as the sole completeness claim; do not rerun merely to quote it.

## 11. Documentation and built assets

Update `docs/frontend.md` and the synchronized Human UI sections of `docs/systems.md` and
`docs/systems.html` in plain language:

- the cache engine manages loaded values and request concurrency;
- the Resource Catalogue names every cached server read, endpoint, type, and event dependency;
- events invalidate through catalogue dependencies;
- a Project rename refreshes Projects, Board, today's Day, backlog Sprint items, Ideas, current Sprint, and
  any opened Ticket detail that either directly names that Project or has not loaded enough to prove
  otherwise; a loaded Ticket with another or no direct Project is excluded, and Project creation and
  summary-only edits refresh Projects only;
- the catalogue keeps a private list of the specific resources opened through its own methods so it can
  check already-opened Ticket details; the cache remains unaware of Projects and Tickets;
- successful UI writes apply one named immediate catalogue effect; and
- the server remains canonical, with no whole-screen/client-store behavior.

Correct the stale frontend sentence that every Ticket event reloads Chat/Hermes trace. All five matching
Chat events invalidate Panels Chat; the four message/turn events invalidate only Chat, while Ticket
`chat_session_created` invalidates exactly Ticket plus Chat because both currently read session state from
the Ticket row. It never invalidates Board, current Sprint, or Review. Ordinary non-Chat Ticket events do not
invalidate Chat. Describe AD09's future Employee/Hermes-history separation as future work, not current fact.

Rebuild `web/dist`, because FastAPI serves it. AD07 has now landed serially before this plan with
`web/dist/assets/index-gWFofsYY.js` as the exact predecessor. The bounded build diff is:

- `web/dist/index.html` changes only to reference the AD08 JavaScript hash;
- `web/dist/assets/index-gWFofsYY.js` is deleted;
- exactly one `web/dist/assets/index-<AD08-hash>.js` is added; and
- CSS, fonts, shared assets, and every other built file remain byte-for-byte unchanged.

Any CSS/font/additional chunk change stops the implementation for inspection rather than widening scope.

## 12. Bounded changed-path allowlist

Implementation may change only:

```text
web/src/lib/resourceCatalogue.ts                         (add)
web/src/lib/resources.svelte.ts
web/src/lib/resources.ts                                (delete)
web/src/lib/eventMapping.mjs                            (delete)
web/src/lib/eventMapping.d.ts                           (delete)
web/src/lib/manifest.svelte.ts                          (delete)
web/src/lib/ws.ts
web/src/App.svelte
web/src/components/ChatPanel.svelte
web/src/routes/BoardRoute.svelte
web/src/routes/BacklogRoute.svelte
web/src/routes/ChiefOfStaffRoute.svelte
web/src/routes/DayRoute.svelte
web/src/routes/IdeasRoute.svelte
web/src/routes/ReviewRoute.svelte
web/src/routes/SprintRoute.svelte
web/src/routes/TicketRoute.svelte
web/tests/resource-catalogue.test.mjs                    (add)
web/tests/resource-cache.test.mjs                        (add)
web/tests/event-mapping.test.mjs                         (delete)
web/package.json
tests/unit/test_frontend_event_mapping.py
tests/unit/test_review_ticket_decisions_type_driven.py
tests/e2e/test_resource_catalogue.py                      (add)
docs/frontend.md
docs/systems.md
docs/systems.html
web/dist/index.html
web/dist/assets/index-gWFofsYY.js                         (delete)
web/dist/assets/index-<AD08-generated-hash>.js            (add exactly one)
```

`web/package.json` already overlaps the serially prior AD07 test-list change. AD08 modifies only that
post-AD07 file's test command to replace the old event-mapping entry and add the two new suites; it must not
undo AD07's Managed Markdown test entry.

Explicitly unchanged and outside the allowlist: `api.ts`, `types.ts`, `lifecycle.ts`, `debug.ts`,
`vite-env.d.ts`, all AD07 source/tests, CSS/tokens/fonts, every backend/database/API/event contract,
`package-lock.json`, all existing e2e preservation files, `PROGRESS.md`, and `decisions.md`.

Any need to add a cached projection, backend/event payload, UI behavior, polling rule, live Worker-type
inference/fallback/default beyond the existing historical migration rewrite,
phantom identity, compatibility alias, extra test hook, or path outside this allowlist returns to the
orchestrator before implementation.
