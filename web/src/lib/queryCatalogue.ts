import { queryOptions } from "@tanstack/svelte-query";
import { fetchJson } from "./api";
import type { ConversationStartValues } from "./conversation/wire";
import type { WorkerTypesResponse } from "./lifecycle";
import type {
  BacklogResponse,
  BoardResponse,
  CurrentSprintResponse,
  DayResponse,
  DeploymentStatus,
  IdeasResponse,
  ProjectsResponse,
  ReviewResponse,
  SkillsHomeResponse,
  SprintsResponse,
  SprintItemsResponse,
  TicketDetail,
  VpsStatusSummary,
  WorkerManagementDetail,
  WorkersResponse
} from "./types";

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
  backlogSprintItems: () =>
    jsonQuery<BacklogResponse>(["items", "backlog"], "/api/items?sprint_id=null"),
  ideas: () => jsonQuery<IdeasResponse>(["ideas"], "/api/ideas"),
  projects: () => jsonQuery<ProjectsResponse>(["projects"], "/api/projects"),
  sprintSummaries: () => jsonQuery<SprintsResponse>(["sprints"], "/api/sprints"),
  sprintItems: () => jsonQuery<SprintItemsResponse>(["items"], "/api/items"),
  currentSprint: () =>
    jsonQuery<CurrentSprintResponse>(["sprint", "current"], "/api/sprint/current"),
  ticket: (ticketId: string) =>
    jsonQuery<TicketDetail>(["ticket", ticketId], `/api/tickets/${encodeURIComponent(ticketId)}`),
  // What a conversation started right now would run on, for each owner that starts one.
  // It follows the change stream because the owner can change it: the Agents screen sets
  // Config sets the Chief's, and a Ticket's own last-chosen values move when its worker
  // is talked to.
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
  workerTypeManifests: () => jsonQuery<WorkerTypesResponse>(["worker-types"], "/api/worker-types"),
  workers: () => jsonQuery<WorkersResponse>(["workers"], "/api/workers"),
  worker: (workerType: string) =>
    jsonQuery<WorkerManagementDetail>(
      ["worker", workerType],
      `/api/workers/${encodeURIComponent(workerType)}`
    ),
  skillsHome: () => jsonQuery<SkillsHomeResponse>(["skills-home"], "/api/skills")
};
