import type { DraftCreationContext, ExecutorHealth, TicketCreationDraft } from "./ticketMachines";
import { MACHINE_HEALTH_POLL_MS } from "./machines/config";
import { queryOptions } from "@tanstack/svelte-query";
import { fetchJson, PlannerFetchError } from "./api";
import { resourceQuery } from "./resourceVersions/query";
import type { FeedbackCountResponse, FeedbackResponse } from "./feedback";
import type { ConversationStartValues } from "./conversation/wire";
import type { UsageDays, UsageSummary } from "./conversation/recordedUsageWire";
import type { WorkerTypesResponse } from "./lifecycle";
import type {
  BoardResponse,
  ChiefManagementSettings,
  ManagerPrincipal,
  ManagerChildFilter,
  DeploymentStatus,
  IdeasResponse,
  NotificationSettingsResponse,
  ProjectsResponse,
  SchedulesResponse,
  SkillsHomeResponse,
  ManagersResponse,
  ManagerWorkspace,
  ManagerWorkspaceContent,
  TicketDetail,
  TicketSummariesResponse,
  VpsStatusSummary,
  WorkerManagementDetail,
  WorkersResponse
} from "./types";

type ChiefConversationReference = {
  conversation_id: string | null;
};

// Every server resource that follows the change stream, in one place: its query
// key and the API path it comes from. A screen names the resource it needs and
// gets both. One-shot reads that nothing invalidates — the employee
// configuration catalog — stay imperative fetches where they are used.
export function jsonQuery<T>(queryKey: readonly unknown[], path: string) {
  return resourceQuery<T>(queryKey, path);
}

export function versionedJsonQuery<T>(queryKey: readonly unknown[], path: string) {
  return resourceQuery<T>(queryKey, path, true);
}

/** Whether a single-type manifest read failed because the type is not installed.
 *
 * The whole-list read answered this by absence from the list. The single-type read
 * answers it with the canonical not-found, and a screen that treats that as an ordinary
 * failure would replace a precise sentence about an unknown Worker type with a generic
 * one. */
export function manifestWorkerTypeIsUnknown(error: unknown): boolean {
  return error instanceof PlannerFetchError && (error.status === 404 || error.code === "not_found");
}

export const queries = {
  ticketCreationContext: (draft: TicketCreationDraft) => queryOptions({
    queryKey: ["ticket-creation-context", draft] as const,
    queryFn: ({ signal }) => fetchJson<DraftCreationContext>("/api/tickets/creation-context", {
      method: "POST", body: draft, signal
    }),
    refetchOnWindowFocus: true,
    retry: false
  }),
  executorHealth: () => ({
    ...jsonQuery<ExecutorHealth>(["executor-health"], "/api/executor/health"),
    refetchInterval: MACHINE_HEALTH_POLL_MS,
    refetchOnWindowFocus: true,
    retry: false
  }),

  recordedUsage: (days: UsageDays, timezone: string) =>
    jsonQuery<UsageSummary>(
      ["recorded-usage", { days, timezone }],
      `/api/conversation/usage/summary?days=${days}&timezone=${encodeURIComponent(timezone)}`
    ),
  deploymentStatus: () =>
    versionedJsonQuery<DeploymentStatus>(["deployment-status"], "/api/deployment-status"),
  vpsStatusSummary: () =>
    jsonQuery<VpsStatusSummary>(["vps-status-summary"], "/api/vps-status-summary"),
  board: () => jsonQuery<BoardResponse>(["board"], "/api/board"),
  managerTicketSummaries: (principal: ManagerPrincipal, filter: ManagerChildFilter, offset: number, limit = 30) =>
    jsonQuery<TicketSummariesResponse>(
      ["tickets", "manager", { principal, filter, offset, limit }],
      `/api/tickets?detail=summary&limit=${limit}&offset=${offset}&manager_kind=${principal.kind}${principal.kind === "manager" ? `&manager_id=${encodeURIComponent(principal.id)}` : ""}${filter === "backlogged" ? "&is_backlogged=true" : filter === "done" ? "&include_terminal=true&stage=done" : "&is_backlogged=false"}`
    ),
  ideas: () => jsonQuery<IdeasResponse>(["ideas"], "/api/ideas"),
  feedback: () => jsonQuery<FeedbackResponse>(["feedback"], "/api/feedback"),
  feedbackCount: () =>
    versionedJsonQuery<FeedbackCountResponse>(["feedback-count"], "/api/feedback/count"),
  projects: () => jsonQuery<ProjectsResponse>(["projects"], "/api/projects?detail=full"),
  schedules: () => jsonQuery<SchedulesResponse>(["schedules"], "/api/schedules"),
  managers: () => jsonQuery<ManagersResponse>(["managers"], "/api/managers?detail=full"),
  managerWorkspaceContent: (itemId: string) =>
    versionedJsonQuery<ManagerWorkspaceContent>(
      ["manager", itemId, "workspace-content"],
      `/api/managers/${encodeURIComponent(itemId)}/workspace-content`
    ),
  managerWorkspace: (itemId: string) =>
    jsonQuery<ManagerWorkspace>(
      ["manager", itemId, "workspace"],
      `/api/managers/${encodeURIComponent(itemId)}/workspace`
    ),
  managerConversationStartValues: (itemId: string) =>
    jsonQuery<ConversationStartValues>(
      ["manager", itemId, "conversation-start-values"],
      `/api/managers/${encodeURIComponent(itemId)}/conversation/start-values`
    ),
  workspaceTicket: (ticketId: string) =>
    versionedJsonQuery<import("./ticketWorkspace/contracts").WorkspaceTicket>(["ticket", ticketId, "workspace"], `/api/tickets/${encodeURIComponent(ticketId)}/workspace`),
  workspaceTicketField: (ticketId: string, field: string) =>
    versionedJsonQuery<import("./ticketWorkspace/contracts").WorkspaceFieldBody>(["ticket", ticketId, "workspace", "field", field], `/api/tickets/${encodeURIComponent(ticketId)}/workspace/fields/${encodeURIComponent(field)}`),
  ticket: (ticketId: string) =>
    jsonQuery<TicketDetail>(["ticket", ticketId], `/api/tickets?detail=full&id=${encodeURIComponent(ticketId)}`),
  reviewProposal: (item: import('./types').NavigationReviewProposalItem) =>
    versionedJsonQuery<import("./ticketWorkspace/contracts").WorkspaceTicket>(["ticket",item.ticket_id,"proposal",item.field,item.proposal_field,item.waiting_since,item.proposal_revision],
      `/api/tickets/${encodeURIComponent(item.ticket_id)}/workspace`),
  // What a conversation started right now would run on, for each owner that starts one.
  // It follows the change stream because the owner can change it: Config sets the
  // Chief's, and a Ticket's own last-chosen values move when its worker is talked to.
  ticketConversationStartValues: (ticketId: string) =>
    jsonQuery<ConversationStartValues>(
      ["ticket", ticketId, "conversation-start-values"],
      `/api/tickets/${encodeURIComponent(ticketId)}/conversation/start-values`
    ),
  chiefSettings: () =>
    versionedJsonQuery<Pick<ChiefManagementSettings, "employee_id" | "label" | "recap" | "skill" | "launch_defaults">>(
      ["chief", "settings"], "/api/workers/chief-of-staff/settings"
    ),
  chiefConversationStartValues: () =>
    versionedJsonQuery<ConversationStartValues>(
      ["chief", "conversation-start-values"],
      "/api/chief/conversation/start-values"
    ),
  chiefConversation: () =>
    queryOptions<ChiefConversationReference>({
      queryKey: ["chief", "conversation"],
      queryFn: ({ signal }) =>
        fetchJson<ChiefConversationReference>("/api/chief/conversation", { signal }),
      // A failed owner lookup is a screen state with an explicit user-controlled retry.
      // Hiding it behind automatic attempts makes a blank/new thread appear authoritative.
      retry: false
    }),
  supportedWorkerTypeManifests: () => versionedJsonQuery<WorkerTypesResponse>(["worker-types", "supported"], "/api/worker-types?supported=true"),
  workerTypeManifests: () => versionedJsonQuery<WorkerTypesResponse>(["worker-types"], "/api/worker-types"),
  /** The manifest of one Worker type, in the shape the whole list arrives in.
   *
   * A screen drawing one Ticket's stages needs one type, and the whole list is 21,544
   * bytes for fourteen of them against 1,865 for one. The answer is the same shape so
   * that nothing downstream has to know which it was handed.
   *
   * Worth knowing before moving another screen onto this: it only pays where the whole
   * list is not already being read. The Ticket page was moved onto it and then moved
   * back, because dogfooding showed the workspace shell around it already reads the list
   * to write a stage sentence on every rail row - so the single-type read added a second
   * request rather than removing the first. The review screen has no rail, reads no list,
   * and saves 91%. Check which it is before switching a screen over. */
  workerTypeManifest: (workerType: string) =>
    versionedJsonQuery<WorkerTypesResponse>(
      ["worker-types", "one", workerType],
      `/api/worker-types/${encodeURIComponent(workerType)}/manifest`
    ),
  workers: () => jsonQuery<WorkersResponse>(["workers"], "/api/workers"),
  worker: (workerType: string) =>
    jsonQuery<WorkerManagementDetail>(
      ["worker", workerType],
      `/api/workers/${encodeURIComponent(workerType)}`
    ),
  skillsHome: () => jsonQuery<SkillsHomeResponse>(["skills-home"], "/api/skills"),
  notificationSettings: () =>
    jsonQuery<NotificationSettingsResponse>(
      ["notification-settings"],
      "/api/notifications/settings"
    )
};
