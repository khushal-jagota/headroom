import { queryOptions } from "@tanstack/svelte-query";
import { fetchJson } from "./api";
import type { FeedbackCountResponse, FeedbackResponse } from "./feedback";
import type { ConversationStartValues } from "./conversation/wire";
import type { WorkerTypesResponse } from "./lifecycle";
import type {
  BoardResponse,
  CurrentSprintResponse,
  DayResponse,
  DeploymentStatus,
  IdeasResponse,
  NotificationSettingsResponse,
  ProjectsResponse,
  ReviewResponse,
  SchedulesResponse,
  SkillsHomeResponse,
  SprintsResponse,
  SprintItemsResponse,
  SprintItemSummariesResponse,
  SprintItemWorkspace,
  SprintTrackingBody,
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
function jsonQuery<T>(queryKey: readonly unknown[], path: string) {
  return queryOptions<T>({
    queryKey,
    queryFn: ({ signal }) => fetchJson<T>(path, { signal })
  });
}

export const queries = {
  deploymentStatus: () =>
    jsonQuery<DeploymentStatus>(["deployment-status"], "/api/deployment-status"),
  vpsStatusSummary: () =>
    jsonQuery<VpsStatusSummary>(["vps-status-summary"], "/api/vps-status-summary"),
  board: () => jsonQuery<BoardResponse>(["board"], "/api/board"),
  review: () => jsonQuery<ReviewResponse>(["review"], "/api/review"),
  todayDay: () => jsonQuery<DayResponse>(["day", "today"], "/api/day/today"),
  backlogTicketSummaries: (offset: number, limit = 30) =>
    jsonQuery<TicketSummariesResponse>(
      ["tickets", "backlog", { limit, offset }],
      `/api/tickets?detail=summary&sprint_id=null&limit=${limit}&offset=${offset}`
    ),
  outcomeSummaries: (offset: number, limit = 30, projectId = "", search = "") =>
    jsonQuery<SprintItemSummariesResponse>(
      ["outcomes", { limit, offset, projectId, search }],
      `/api/items?detail=summary&limit=${limit}&offset=${offset}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}${search ? `&search=${encodeURIComponent(search)}` : ""}`
    ),
  ideas: () => jsonQuery<IdeasResponse>(["ideas"], "/api/ideas"),
  feedback: () => jsonQuery<FeedbackResponse>(["feedback"], "/api/feedback"),
  feedbackCount: () =>
    jsonQuery<FeedbackCountResponse>(["feedback-count"], "/api/feedback/count"),
  projects: () => jsonQuery<ProjectsResponse>(["projects"], "/api/projects?detail=full"),
  schedules: () => jsonQuery<SchedulesResponse>(["schedules"], "/api/schedules"),
  sprintSummaries: () => jsonQuery<SprintsResponse>(["sprints"], "/api/sprints?detail=full"),
  sprintItems: () => jsonQuery<SprintItemsResponse>(["items"], "/api/items?detail=full"),
  currentSprint: () =>
    jsonQuery<CurrentSprintResponse>(["sprint", "current"], "/api/sprint/current"),
  sprintTracking: (sprintId: string) =>
    jsonQuery<SprintTrackingBody>(
      ["sprint", sprintId, "tracking"],
      `/api/sprints/${encodeURIComponent(sprintId)}/tracking`
    ),
  sprintItemWorkspace: (itemId: string) =>
    jsonQuery<SprintItemWorkspace>(
      ["sprint-item", itemId, "workspace"],
      `/api/items/${encodeURIComponent(itemId)}/workspace`
    ),
  sprintItemConversationStartValues: (itemId: string) =>
    jsonQuery<ConversationStartValues>(
      ["sprint-item", itemId, "conversation-start-values"],
      `/api/items/${encodeURIComponent(itemId)}/supervisor/conversation/start-values`
    ),
  ticket: (ticketId: string) =>
    jsonQuery<TicketDetail>(["ticket", ticketId], `/api/tickets?detail=full&id=${encodeURIComponent(ticketId)}`),
  // What a conversation started right now would run on, for each owner that starts one.
  // It follows the change stream because the owner can change it: Config sets the
  // Chief's, and a Ticket's own last-chosen values move when its worker is talked to.
  ticketConversationStartValues: (ticketId: string) =>
    jsonQuery<ConversationStartValues>(
      ["ticket", ticketId, "conversation-start-values"],
      `/api/tickets/${encodeURIComponent(ticketId)}/conversation/start-values`
    ),
  chiefConversationStartValues: () =>
    jsonQuery<ConversationStartValues>(
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
  workerTypeManifests: () => jsonQuery<WorkerTypesResponse>(["worker-types"], "/api/worker-types"),
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
