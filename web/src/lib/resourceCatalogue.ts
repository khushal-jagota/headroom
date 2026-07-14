import { fetchJson, type FetchOptions } from "./api";
import type { WorkerTypesResponse } from "./lifecycle";
import {
  invalidateMany,
  peek,
  refresh,
  resource,
  type ResourceHandle
} from "./resources.svelte";
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

type EventFacts = Readonly<{
  event: PlannerEvent;
  prefix: EventEntityPrefix;
  context: ResourceEventContext;
  ticketChatKind: boolean;
  panelsChatKind: boolean;
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
  "agent"
];

const TICKET_CHAT_KINDS = new Set([
  "chat_message_recorded",
  "chat_turn_started",
  "chat_turn_updated",
  "chat_turn_finished"
]);

const PANELS_CHAT_KINDS = new Set([
  "chat_session_created",
  ...TICKET_CHAT_KINDS
]);

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
  prefix: "ticket" | "chat" | "chat-status",
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
  return (
    facts.prefix === "t" &&
    !facts.ticketChatKind &&
    !facts.employeeSessionChanged &&
    facts.event.kind !== "chat_session_created"
  );
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
      (eventEntityId(facts, "day") && facts.currentDayEvent && !facts.panelsChatKind) ||
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
  panelsChat: parameterizedDefinition<ChatStateResponse>(
    "chat",
    (entityId) => `/api/chat/${encodeURIComponent(entityId)}/state`,
    {
      eventInvalidated: true,
      refreshCachedOnOpen: true,
      affectedByEvent: (facts) => {
        if ((facts.prefix === "day" || facts.prefix === "agent") && facts.panelsChatKind) {
          return [`chat:${facts.event.entity_id}`];
        }
        return facts.prefix === "t" && facts.ticketChatKind
          ? [`chat:${facts.event.entity_id}`]
          : [];
      }
    }
  ),
  chatGatewayStatus: parameterizedDefinition<GatewayStatus>(
    "chat-status",
    (entityId) => `/api/chat/${encodeURIComponent(entityId)}/status`,
    { eventInvalidated: false }
  ),
  chatCommands: staticDefinition<CommandCatalog>("chat-commands", "/api/chat/commands", {
    eventInvalidated: false
  }),
  workerTypeManifests: staticDefinition<WorkerTypesResponse>(
    "worker-types",
    "/api/worker-types",
    { eventInvalidated: false }
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
  panelsChat: (entityId) => {
    const id = requireId(entityId, "entityId");
    return openResource(RESOURCE_DEFINITIONS.panelsChat, id);
  },
  chatGatewayStatus: (entityId) => {
    const id = requireId(entityId, "entityId");
    return openResource(RESOURCE_DEFINITIONS.chatGatewayStatus, id);
  },
  chatCommands: () => openResource(RESOURCE_DEFINITIONS.chatCommands),
  workerTypeManifests: () => openResource(RESOURCE_DEFINITIONS.workerTypeManifests)
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
    ticketChatKind: prefix === "t" && TICKET_CHAT_KINDS.has(event.kind),
    panelsChatKind: PANELS_CHAT_KINDS.has(event.kind),
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
      return { identities: ticketEffectIdentities(effect.ticketId, false, false), refreshReview: false };
    case "ticketTitleChanged":
    case "ticketReviewStateChanged":
      return { identities: ticketEffectIdentities(effect.ticketId, true, false), refreshReview: false };
    case "reviewTicketAccepted":
      return { identities: ticketEffectIdentities(effect.ticketId, false, false), refreshReview: true };
    case "reviewTicketReturnedForRevision":
      return { identities: ticketEffectIdentities(effect.ticketId, false, true), refreshReview: true };
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
  }
  return assertNever(effect);
}

function ticketEffectIdentities(
  ticketId: string,
  includeReview: boolean,
  includeChat: boolean
): CatalogueResourceIdentity[] {
  const id = requireId(ticketId, "ticketId");
  return [
    RESOURCE_DEFINITIONS.ticket.identity(id),
    ...(includeChat ? [RESOURCE_DEFINITIONS.panelsChat.identity(id)] : []),
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
