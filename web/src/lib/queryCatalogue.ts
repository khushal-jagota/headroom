import { queryOptions } from "@tanstack/svelte-query";
import { fetchJson } from "./api";
import type { WorkerTypesResponse } from "./lifecycle";
import type {
  BacklogResponse,
  BoardResponse,
  CurrentSprintResponse,
  DayResponse,
  IdeasResponse,
  ProjectsResponse,
  ReviewResponse,
  SkillsHomeResponse,
  SprintsResponse,
  TicketDetail,
  WorkerManagementDetail,
  WorkersResponse
} from "./types";

// Every server resource that follows the change stream, in one place: its query
// key and the API path it comes from. A screen names the resource it needs and
// gets both. One-shot reads that nothing invalidates — the employee
// configuration catalog, the VPS status snapshot — stay imperative fetches
// where they are used.
function jsonQuery<T>(queryKey: readonly unknown[], path: string) {
  return queryOptions<T>({
    queryKey,
    queryFn: ({ signal }) => fetchJson<T>(path, { signal })
  });
}

export const queries = {
  board: () => jsonQuery<BoardResponse>(["board"], "/api/board"),
  review: () => jsonQuery<ReviewResponse>(["review"], "/api/review"),
  todayDay: () => jsonQuery<DayResponse>(["day", "today"], "/api/day/today"),
  backlogSprintItems: () =>
    jsonQuery<BacklogResponse>(["items", "backlog"], "/api/items?sprint_id=null"),
  ideas: () => jsonQuery<IdeasResponse>(["ideas"], "/api/ideas"),
  projects: () => jsonQuery<ProjectsResponse>(["projects"], "/api/projects"),
  sprintSummaries: () => jsonQuery<SprintsResponse>(["sprints"], "/api/sprints"),
  currentSprint: () =>
    jsonQuery<CurrentSprintResponse>(["sprint", "current"], "/api/sprint/current"),
  ticket: (ticketId: string) =>
    jsonQuery<TicketDetail>(["ticket", ticketId], `/api/tickets/${encodeURIComponent(ticketId)}`),
  workerTypeManifests: () => jsonQuery<WorkerTypesResponse>(["worker-types"], "/api/worker-types"),
  workers: () => jsonQuery<WorkersResponse>(["workers"], "/api/workers"),
  worker: (workerType: string) =>
    jsonQuery<WorkerManagementDetail>(
      ["worker", workerType],
      `/api/workers/${encodeURIComponent(workerType)}`
    ),
  skillsHome: () => jsonQuery<SkillsHomeResponse>(["skills-home"], "/api/skills")
};
