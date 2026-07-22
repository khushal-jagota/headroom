import { fetchJson, type FetchOptions } from "./api";
import type { WorkerTypesResponse } from "./lifecycle";
import {
  invalidateMany,
  peek,
  refresh,
  resource,
  subscribedResourceKeys,
  type ResourceHandle
} from "./resources.svelte";
import type {
  BacklogResponse,
  BoardResponse,
  CurrentSprintResponse,
  DayResponse,
  IdeasResponse,
  ProjectsResponse,
  ReviewResponse,
  SprintsResponse,
  TicketDetail,
  WorkerManagementDetail,
  WorkersResponse
} from "./types";

export type EventEntityPrefix =
  | "t"
  | "si"
  | "sp"
  | "day"
  | "idea"
  | "project"
  | "agent"
  | "worker";

export type CatalogueResourceIdentity =
  | "board"
  | "review"
  | "day:today"
  | "items:backlog"
  | "ideas"
  | "projects"
  | "sprints"
  | "sprint:current"
  | "worker-types"
  | "workers"
  | `ticket:${string}`
  | `worker:${string}`;

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
  | Readonly<{ kind: "currentSprintChanged" }>
  | Readonly<{ kind: "workerSettingsChanged"; workerType: string }>;

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
  workerTypeManifests(): ResourceHandle<WorkerTypesResponse>;
  workers(): ResourceHandle<WorkersResponse>;
  worker(workerType: string): ResourceHandle<WorkerManagementDetail>;
}

type EventFacts = Readonly<{
  event: PlannerEvent;
  prefix: EventEntityPrefix;
  context: ResourceEventContext;
  employeeSessionChanged: boolean;
  projectNameChanged: boolean;
  relatedEntityIds: readonly string[];
  membershipTicketId: string | null;
  currentDayEvent: boolean;
}>;

type ResourceDefinition<T, Params extends readonly string[]> = Readonly<{
  identity: (...params: Params) => CatalogueResourceIdentity;
  recognizes: (identity: string) => boolean;
  path: (...params: Params) => string;
  parameterized: boolean;
  eventInvalidated: boolean;
  refreshCachedOnOpen: boolean;
  affectedByEvent?: (facts: EventFacts) => CatalogueResourceIdentity[];
}>;

const ENTITY_PREFIXES: readonly EventEntityPrefix[] = [
  "t",
  "si",
  "sp",
  "day",
  "idea",
  "project",
  "agent",
  "worker"
];

const REVIEW_TICKET_EVENT_KINDS = new Set([
  "stage_changed",
  "proposal_accepted",
  "proposal_superseded",
  "proposal_filed",
  "kickoff_proposal_filed",
  "kickoff_accepted",
  "approval_returned",
  "stage_ownership_changed",
  "ticket_status_changed",
  "ticket_deleted"
]);

const OPENED_PARAMETERIZED_IDENTITIES = new Set<CatalogueResourceIdentity>();

function staticDefinition<T>(
  identity: CatalogueResourceIdentity,
  path: string,
  options: {
    eventInvalidated: boolean;
    refreshCachedOnOpen?: boolean;
    affectedByEvent?: (facts: EventFacts) => CatalogueResourceIdentity[];
  }
): ResourceDefinition<T, readonly []> {
  return {
    identity: () => identity,
    recognizes: (candidate) => candidate === identity,
    path: () => path,
    parameterized: false,
    eventInvalidated: options.eventInvalidated,
    refreshCachedOnOpen: options.refreshCachedOnOpen ?? false,
    affectedByEvent: options.affectedByEvent
  };
}

function parameterizedDefinition<T>(
  prefix: "ticket" | "worker",
  path: (id: string) => string,
  options: {
    eventInvalidated: boolean;
    refreshCachedOnOpen?: boolean;
    affectedByEvent?: (facts: EventFacts) => CatalogueResourceIdentity[];
  }
): ResourceDefinition<T, readonly [string]> {
  return {
    identity: (id) => `${prefix}:${id}` as CatalogueResourceIdentity,
    recognizes: (candidate) => candidate.startsWith(`${prefix}:`) && candidate.length > prefix.length + 1,
    path,
    parameterized: true,
    eventInvalidated: options.eventInvalidated,
    refreshCachedOnOpen: options.refreshCachedOnOpen ?? false,
    affectedByEvent: options.affectedByEvent
  };
}

function eventEntityId(facts: EventFacts, prefix: EventEntityPrefix): boolean {
  return facts.prefix === prefix;
}

function relatedHasPrefix(facts: EventFacts, prefix: EventEntityPrefix): boolean {
  return facts.relatedEntityIds.some((entityId) => entityPrefix(entityId) === prefix);
}

function ordinaryTicketEvent(facts: EventFacts): boolean {
  return facts.prefix === "t" && !facts.employeeSessionChanged;
}

function projectNameAggregate(facts: EventFacts): boolean {
  return facts.projectNameChanged;
}

function ticketIdentitiesForProjectName(facts: EventFacts): CatalogueResourceIdentity[] {
  if (!facts.projectNameChanged) return [];
  const identities: CatalogueResourceIdentity[] = [];
  for (const identity of OPENED_PARAMETERIZED_IDENTITIES) {
    if (!RESOURCE_DEFINITIONS.ticket.recognizes(identity)) continue;
    const detail = peek<TicketDetail>(identity);
    if (detail === undefined || detail.project_id === facts.event.entity_id) identities.push(identity);
  }
  return identities;
}

const RESOURCE_DEFINITIONS = {
  board: staticDefinition<BoardResponse>("board", "/api/board", {
    eventInvalidated: true,
    affectedByEvent: (facts) =>
      ordinaryTicketEvent(facts) ||
      eventEntityId(facts, "si") ||
      relatedHasPrefix(facts, "t") ||
      relatedHasPrefix(facts, "si") ||
      facts.membershipTicketId !== null ||
      projectNameAggregate(facts)
        ? ["board"]
        : []
  }),
  review: staticDefinition<ReviewResponse>("review", "/api/review", {
    eventInvalidated: true,
    affectedByEvent: (facts) => {
      if (
        ordinaryTicketEvent(facts) &&
        (REVIEW_TICKET_EVENT_KINDS.has(facts.event.kind) ||
          (facts.event.kind === "ticket_updated" && facts.event.payload.field === "title"))
      ) {
        return ["review"];
      }
      if (facts.membershipTicketId !== null && facts.currentDayEvent) return ["review"];
      return [];
    }
  }),
  todayDay: staticDefinition<DayResponse>("day:today", "/api/day/today", {
    eventInvalidated: true,
    affectedByEvent: (facts) =>
      (eventEntityId(facts, "day") && facts.currentDayEvent) ||
      projectNameAggregate(facts)
        ? ["day:today"]
        : []
  }),
  backlogSprintItems: staticDefinition<BacklogResponse>(
    "items:backlog",
    "/api/items?sprint_id=null",
    {
      eventInvalidated: true,
      affectedByEvent: (facts) =>
        eventEntityId(facts, "si") || relatedHasPrefix(facts, "si") || projectNameAggregate(facts)
          ? ["items:backlog"]
          : []
    }
  ),
  ideas: staticDefinition<IdeasResponse>("ideas", "/api/ideas", {
    eventInvalidated: true,
    affectedByEvent: (facts) =>
      eventEntityId(facts, "idea") || projectNameAggregate(facts) ? ["ideas"] : []
  }),
  projects: staticDefinition<ProjectsResponse>("projects", "/api/projects", {
    eventInvalidated: true,
    affectedByEvent: (facts) => (eventEntityId(facts, "project") ? ["projects"] : [])
  }),
  sprintSummaries: staticDefinition<SprintsResponse>("sprints", "/api/sprints", {
    eventInvalidated: true,
    affectedByEvent: (facts) => (eventEntityId(facts, "sp") ? ["sprints"] : [])
  }),
  currentSprint: staticDefinition<CurrentSprintResponse>(
    "sprint:current",
    "/api/sprint/current",
    {
      eventInvalidated: true,
      affectedByEvent: (facts) =>
        ordinaryTicketEvent(facts) ||
        eventEntityId(facts, "si") ||
        eventEntityId(facts, "sp") ||
        relatedHasPrefix(facts, "t") ||
        relatedHasPrefix(facts, "si") ||
        projectNameAggregate(facts)
          ? ["sprint:current"]
          : []
    }
  ),
  ticket: parameterizedDefinition<TicketDetail>(
    "ticket",
    (ticketId) => `/api/tickets/${encodeURIComponent(ticketId)}`,
    {
      eventInvalidated: true,
      affectedByEvent: (facts) => {
        const identities: CatalogueResourceIdentity[] = [];
        if (ordinaryTicketEvent(facts) || facts.employeeSessionChanged) {
          identities.push(`ticket:${facts.event.entity_id}`);
        }
        for (const entityId of facts.relatedEntityIds) {
          if (entityPrefix(entityId) === "t") identities.push(`ticket:${entityId}`);
        }
        if (facts.membershipTicketId !== null) {
          identities.push(`ticket:${facts.membershipTicketId}`);
        }
        identities.push(...ticketIdentitiesForProjectName(facts));
        return identities;
      }
    }
  ),
  workerTypeManifests: staticDefinition<WorkerTypesResponse>(
    "worker-types",
    "/api/worker-types",
    { eventInvalidated: false }
  ),
  workers: staticDefinition<WorkersResponse>("workers", "/api/workers", {
    eventInvalidated: true,
    affectedByEvent: (facts) => (eventEntityId(facts, "worker") ? ["workers"] : [])
  }),
  worker: parameterizedDefinition<WorkerManagementDetail>(
    "worker",
    (workerType) => `/api/workers/${encodeURIComponent(workerType)}`,
    {
      eventInvalidated: true,
      affectedByEvent: (facts) =>
        eventEntityId(facts, "worker")
          ? [`worker:${facts.event.entity_id.slice("worker_".length)}`]
          : []
    }
  )
} as const;

function requireId(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function openResource<T, Params extends readonly string[]>(
  definition: ResourceDefinition<T, Params>,
  ...params: Params
): ResourceHandle<T> {
  const identity = definition.identity(...params);
  if (definition.parameterized) OPENED_PARAMETERIZED_IDENTITIES.add(identity);
  const handle = resource<T>(identity, (signal) => fetchJson(definition.path(...params), { signal }));
  if (definition.refreshCachedOnOpen && handle.data !== undefined && !handle.stale) {
    void refresh(identity)?.catch(() => undefined);
  }
  return handle;
}

export const resourceCatalogue: ResourceCatalogue = {
  board: () => openResource(RESOURCE_DEFINITIONS.board),
  review: () => openResource(RESOURCE_DEFINITIONS.review),
  todayDay: () => openResource(RESOURCE_DEFINITIONS.todayDay),
  backlogSprintItems: () => openResource(RESOURCE_DEFINITIONS.backlogSprintItems),
  ideas: () => openResource(RESOURCE_DEFINITIONS.ideas),
  projects: () => openResource(RESOURCE_DEFINITIONS.projects),
  sprintSummaries: () => openResource(RESOURCE_DEFINITIONS.sprintSummaries),
  currentSprint: () => openResource(RESOURCE_DEFINITIONS.currentSprint),
  ticket: (ticketId) => {
    const id = requireId(ticketId, "ticketId");
    return openResource(RESOURCE_DEFINITIONS.ticket, id);
  },
  workerTypeManifests: () => openResource(RESOURCE_DEFINITIONS.workerTypeManifests),
  workers: () => openResource(RESOURCE_DEFINITIONS.workers),
  worker: (workerType) => {
    const id = requireId(workerType, "workerType");
    return openResource(RESOURCE_DEFINITIONS.worker, id);
  }
};

function entityPrefix(entityId: unknown): EventEntityPrefix {
  if (typeof entityId !== "string") throw new Error(`unknown entity_id prefix: ${String(entityId)}`);
  const match = /^([^_]+)_(.+)$/.exec(entityId);
  if (!match || !ENTITY_PREFIXES.includes(match[1] as EventEntityPrefix)) {
    throw new Error(`unknown entity_id prefix: ${entityId}`);
  }
  return match[1] as EventEntityPrefix;
}

function eventPayload(event: PlannerEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" ? event.payload : {};
}

function relatedEntityIds(event: PlannerEvent): string[] {
  const payload = eventPayload(event);
  const related: string[] = [];
  if (event.kind === "link_added" || event.kind === "link_removed") {
    for (const value of [payload.from_id, payload.to_id]) {
      if (value === undefined || value === null || value === "") continue;
      const id = String(value);
      const prefix = entityPrefix(id);
      if (prefix !== "t" && prefix !== "si") throw new Error(`unknown entity_id prefix: ${id}`);
      related.push(id);
    }
  }
  if (Array.isArray(payload.affected_blocked_target_ids)) {
    for (const value of payload.affected_blocked_target_ids) {
      const id = String(value);
      const prefix = entityPrefix(id);
      if (prefix !== "t" && prefix !== "si") throw new Error(`unknown entity_id prefix: ${id}`);
      related.push(id);
    }
  }
  return related;
}

function isProjectNameChange(event: PlannerEvent, prefix: EventEntityPrefix): boolean {
  const fields = eventPayload(event).fields;
  return (
    prefix === "project" &&
    event.kind === "project_updated" &&
    Array.isArray(fields) &&
    fields.includes("name")
  );
}

function currentDayEvent(event: PlannerEvent, context: ResourceEventContext): boolean {
  return context.todayDayId === null || context.todayDayId === event.entity_id;
}

function factsForEvent(
  event: PlannerEvent,
  context: ResourceEventContext | undefined
): EventFacts {
  if (!event || typeof event !== "object") throw new Error("event object is required");
  const prefix = entityPrefix(event.entity_id);
  const resolvedContext =
    context ?? { todayDayId: peek<DayResponse>("day:today")?.id ?? null };
  const membership =
    (event.kind === "day_ticket_added" || event.kind === "day_ticket_removed") &&
    typeof eventPayload(event).ticket_id === "string" &&
    eventPayload(event).ticket_id
      ? String(eventPayload(event).ticket_id)
      : null;
  if (membership !== null && entityPrefix(membership) !== "t") {
    throw new Error(`unknown entity_id prefix: ${membership}`);
  }
  return {
    event,
    prefix,
    context: resolvedContext,
    employeeSessionChanged: prefix === "t" && event.kind === "employee_session_changed",
    projectNameChanged: isProjectNameChange(event, prefix),
    relatedEntityIds: relatedEntityIds(event),
    membershipTicketId: membership,
    currentDayEvent: prefix === "day" && currentDayEvent(event, resolvedContext)
  };
}

function allDefinitions(): readonly ResourceDefinition<unknown, readonly string[]>[] {
  return Object.values(RESOURCE_DEFINITIONS) as readonly ResourceDefinition<
    unknown,
    readonly string[]
  >[];
}

function recognizesIdentity(identity: string): boolean {
  return allDefinitions().some((definition) => definition.recognizes(identity));
}

function validateIdentity(identity: string): asserts identity is CatalogueResourceIdentity {
  if (!recognizesIdentity(identity)) {
    throw new Error(`unknown catalogue resource identity: ${identity}`);
  }
}

export function keysForEvent(
  event: PlannerEvent,
  context?: ResourceEventContext
): CatalogueResourceIdentity[] {
  const facts = factsForEvent(event, context);
  const identities: CatalogueResourceIdentity[] = [];
  for (const definition of allDefinitions()) {
    if (!definition.eventInvalidated || !definition.affectedByEvent) continue;
    identities.push(...definition.affectedByEvent(facts));
  }
  const unique = Array.from(new Set(identities));
  for (const identity of unique) validateIdentity(identity);
  return unique;
}

export function invalidateCatalogueResources(
  identities: readonly CatalogueResourceIdentity[],
  reason: string
): void {
  for (const identity of identities) validateIdentity(identity);
  invalidateMany(Array.from(new Set(identities)), reason);
}

export async function reconcileSubscribedCatalogueResources(): Promise<void> {
  const identities = Array.from(
    new Set(subscribedResourceKeys().filter(recognizesIdentity))
  ) as CatalogueResourceIdentity[];
  await Promise.all(identities.map((identity) => refresh(identity)?.catch(() => undefined)));
}

export function knownEntityPrefixes(): readonly EventEntityPrefix[] {
  return ENTITY_PREFIXES;
}

type MutationEffectPlan = Readonly<{
  identities: readonly CatalogueResourceIdentity[];
  refreshReview: boolean;
}>;

function mutationEffectPlan(effect: ResourceMutationEffect): MutationEffectPlan {
  switch (effect.kind) {
    case "ideaCreated":
      return { identities: [RESOURCE_DEFINITIONS.ideas.identity()], refreshReview: false };
    case "backlogSprintItemCreated":
      return {
        identities: [
          RESOURCE_DEFINITIONS.backlogSprintItems.identity(),
          RESOURCE_DEFINITIONS.currentSprint.identity(),
          RESOURCE_DEFINITIONS.board.identity()
        ],
        refreshReview: false
      };
    case "ticketChanged":
      return { identities: ticketEffectIdentities(effect.ticketId, false), refreshReview: false };
    case "ticketTitleChanged":
    case "ticketReviewStateChanged":
      return { identities: ticketEffectIdentities(effect.ticketId, true), refreshReview: false };
    case "reviewTicketAccepted":
      return { identities: ticketEffectIdentities(effect.ticketId, false), refreshReview: true };
    case "reviewTicketReturnedForRevision":
      return { identities: ticketEffectIdentities(effect.ticketId, false), refreshReview: true };
    case "todayDayChanged":
      return { identities: [RESOURCE_DEFINITIONS.todayDay.identity()], refreshReview: false };
    case "currentSprintChanged":
      return {
        identities: [
          RESOURCE_DEFINITIONS.currentSprint.identity(),
          RESOURCE_DEFINITIONS.sprintSummaries.identity()
        ],
        refreshReview: false
      };
    case "workerSettingsChanged": {
      const workerType = requireId(effect.workerType, "workerType");
      return {
        identities: [
          RESOURCE_DEFINITIONS.workers.identity(),
          RESOURCE_DEFINITIONS.worker.identity(workerType)
        ],
        refreshReview: false
      };
    }
  }
  return assertNever(effect);
}

function ticketEffectIdentities(
  ticketId: string,
  includeReview: boolean
): CatalogueResourceIdentity[] {
  const id = requireId(ticketId, "ticketId");
  return [
    RESOURCE_DEFINITIONS.ticket.identity(id),
    RESOURCE_DEFINITIONS.board.identity(),
    RESOURCE_DEFINITIONS.currentSprint.identity(),
    ...(includeReview ? [RESOURCE_DEFINITIONS.review.identity()] : [])
  ];
}

function assertNever(value: never): never {
  throw new Error(`unreachable mutation effect: ${String(value)}`);
}

export function mutateJsonWithResourceEffect<T = unknown>(
  path: string,
  options: FetchOptions,
  effect: ResourceMutationEffect
): Promise<T> {
  return fetchJson<T>(path, options).then(async (result) => {
    const plan = mutationEffectPlan(effect);
    invalidateCatalogueResources(plan.identities, "successful mutation");
    if (plan.refreshReview) {
      await refresh<ReviewResponse>(RESOURCE_DEFINITIONS.review.identity())?.catch(() => undefined);
    }
    return result;
  });
}

export type { ResourceHandle } from "./resources.svelte";
