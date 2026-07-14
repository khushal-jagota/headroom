# AD08 contract lock

The orchestrator generated this lock after the corrected delegated implementation plan passed independent
read-only review. AD08 concentrates cached-read identity and dependency knowledge in one frontend Resource
Catalogue. It does not change server authority, UI behavior, backend contracts, or Worker-type semantics.

## One catalogue and exact public API

`web/src/lib/resourceCatalogue.ts` is the only production owner of cached projection identities, GET paths,
response types, event dependencies, refresh-on-open policy, and successful-mutation resource effects. It
exports exactly the declarations frozen in plan section 3:

```ts
export type EventEntityPrefix =
  | "t" | "si" | "sp" | "day" | "idea" | "project" | "agent";

export type CatalogueResourceIdentity =
  | "board" | "review" | "day:today" | "items:backlog" | "ideas"
  | "projects" | "sprints" | "sprint:current" | "chat-commands" | "worker-types"
  | `ticket:${string}` | `chat:${string}` | `chat-status:${string}`;

export type PlannerEvent = Readonly<{
  id: number;
  entity_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: number;
}>;

export type ResourceEventContext = Readonly<{ todayDayId: string | null }>;

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

The existing response types are imported, never redeclared. Ticket and Chat ids must be non-empty strings,
are preserved exactly in identities, and use `encodeURIComponent` only in path segments. There is no trim,
lowercase, reinterpretation, or default. The closed effect union is exhaustive and has no runtime fallback.

## Exact resource inventory and engine boundary

One private `RESOURCE_DEFINITIONS` object declares exactly these cached projections:

| Method | Identity | GET path |
| --- | --- | --- |
| `board()` | `board` | `/api/board` |
| `review()` | `review` | `/api/review` |
| `todayDay()` | `day:today` | `/api/day/today` |
| `backlogSprintItems()` | `items:backlog` | `/api/items?sprint_id=null` |
| `ideas()` | `ideas` | `/api/ideas` |
| `projects()` | `projects` | `/api/projects` |
| `sprintSummaries()` | `sprints` | `/api/sprints` |
| `currentSprint()` | `sprint:current` | `/api/sprint/current` |
| `ticket(id)` | `ticket:<id>` | `/api/tickets/<encoded-id>` |
| `panelsChat(id)` | `chat:<id>` | `/api/chat/<encoded-id>/state` |
| `chatGatewayStatus(id)` | `chat-status:<id>` | `/api/chat/<encoded-id>/status` |
| `chatCommands()` | `chat-commands` | `/api/chat/commands` |
| `workerTypeManifests()` | `worker-types` | `/api/worker-types` |

There is no cached individual Sprint, Sprint item, or dated Day, and therefore no `sprint:<id>`,
`item:<id>`, or `day:<date>` identity. Mutations, uploads, managed-file reads, copy text, Chat delivery, and
startup `/api/meta` remain ordinary requests outside the catalogue.

`resources.svelte.ts` remains the semantic-free cache engine. It keeps de-duplication, forced-refresh
supersession, stale-completion rejection, last-good data, subscriber behavior, disposal, structured errors,
batch de-duplication, `peek`, and keyed refresh. It has no Project, Ticket, event, or mutation-effect
knowledge. Delete its public handle `invalidate()` and free-form `mutateJson` surface. Only the catalogue
uses the generic factory in production. `panelsChat` alone refreshes already-fresh cached data on open.

## Event dependency contract

Ordinary synthetic or future kinds use the entity prefix without a per-kind table:

| Prefix | Exact primary identities |
| --- | --- |
| `t` | matching Ticket, Board, current Sprint |
| `si` | backlog Sprint items, Board, current Sprint |
| `sp` | Sprint summaries, current Sprint |
| `day` | today's Day only when matching today or before today's id is cached |
| `idea` | Ideas |
| `project` | Projects |
| `agent` | matching Panels Chat |

Unknown or malformed prefixes fail. Every returned identity is validated through the same definitions.
Link endpoints, affected-blocked target ids, Day membership Ticket ids, current-day Review membership, and
AD04's narrow Review Ticket/title events remain the exact reviewed exceptions.

The five Chat event kinds take precedence over ordinary Ticket behavior. For a Ticket entity,
`chat_session_created` invalidates exactly its Ticket plus its Panels Chat; the four message/turn events
invalidate only its Panels Chat. No Chat event reaches Board, current Sprint, Review, status, commands, or
manifests. This temporary Ticket/Chat union is required because Employee claim, human binding, and legacy
history remint still write the same Ticket session field until AD09.

Project creation, summary-only `project_updated`, and synthetic Project kinds invalidate Projects only. A
`project_updated` whose `payload.fields` contains `name` invalidates exactly Projects, Board, today's Day,
backlog Sprint items, Ideas, and current Sprint, plus already-opened Ticket details selected by the
catalogue's private process-lifetime parameterized-identity index. An unloaded opened Ticket is included
conservatively; a loaded Ticket is included only when its direct `project_id` matches. Loaded unrelated,
null, or absent direct Project ids are excluded. Review, Chat, Sprint summaries, status, commands, and
manifests are never included. The generic cache does not own or populate this index.

## Immediate effects and ordering

Successful writes resolve exactly the nine named effects in the plan. A failed write performs no cache
action. Ordinary success invalidates the de-duplicated exact identities immediately. Review accept and
return exclude Review from ordinary invalidation, then await exactly one keyed Review refresh; only that
refresh error is swallowed. Return-for-revision includes matching Panels Chat; accept does not. Routes pass
effect values, never resource identities or arrays. Chat polling, start, Pause, and explicit stale-self-heal
retain their current handle-refresh sequencing.

## Deletion, tests, and scope

Delete `eventMapping.mjs/.d.ts`, `resources.ts`, `manifest.svelte.ts`, the old event-mapping test, route-local
cached GET declarations, invalidation arrays/constants, `refreshReviewAfter`, phantom identities, and all
compatibility aliases. RED tests must prove the catalogue interface and anti-rot rules, all effects and
ordering, generic engine behavior, two-signal browser behavior, shared handles, Project-name propagation,
and shared-session precision before rewiring production.

Implementation may change only the exact allowlist in reviewed plan section 12. It may not change backend,
database, API, event payloads, UI behavior, polling, AD07, or AD09. Checked-in `web/dist` is rebuilt from the
reviewed source: predecessor `index-gWFofsYY.js` is replaced by exactly one new JavaScript chunk; CSS, fonts,
and other assets remain byte-identical.

Every live Ticket creation path continues to require an explicit Worker type. No runtime signature,
request, storage column, resource, or catalogue behavior may infer or default to `coding`. The existing
forward migration may rewrite an old row that predates stored Worker type to `coding`; that historical
classification is the sole allowed concession.
