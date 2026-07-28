import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJson } from "../src/lib/api";
import type { FetchOptions } from "../src/lib/api";
import { queries } from "../src/lib/queryCatalogue";

vi.mock("../src/lib/api", () => ({
  fetchJson: vi.fn()
}));

type QueryDefinition = {
  queryKey: readonly unknown[];
  queryFn?: unknown;
};

const mockedFetchJson = vi.mocked(fetchJson);

async function executeQuery(query: QueryDefinition, signal: AbortSignal): Promise<void> {
  if (typeof query.queryFn !== "function") {
    throw new Error("query has no executable query function");
  }
  await query.queryFn({ signal });
}

function lastFetch(): [path: string, options?: FetchOptions] {
  const call = mockedFetchJson.mock.calls.at(-1);
  if (!call) {
    throw new Error("query did not call fetchJson");
  }
  return call;
}

beforeEach(() => {
  mockedFetchJson.mockReset();
  mockedFetchJson.mockImplementation(async (path) => ({ path }));
});

describe("query catalogue", () => {
  it.each([
    ["board", () => queries.board(), ["board"], "/api/board"],
    ["review", () => queries.review(), ["review"], "/api/review"],
    ["today's day", () => queries.todayDay(), ["day", "today"], "/api/day/today"],
    [
      "backlog sprint items",
      () => queries.backlogSprintItems(),
      ["items", "backlog"],
      "/api/items?sprint_id=null"
    ],
    ["ideas", () => queries.ideas(), ["ideas"], "/api/ideas"],
    ["projects", () => queries.projects(), ["projects"], "/api/projects"],
    ["sprint summaries", () => queries.sprintSummaries(), ["sprints"], "/api/sprints"],
    ["sprint items", () => queries.sprintItems(), ["items"], "/api/items"],
    [
      "current sprint",
      () => queries.currentSprint(),
      ["sprint", "current"],
      "/api/sprint/current"
    ],
    [
      "Chief conversation start values",
      () => queries.chiefConversationStartValues(),
      ["chief", "conversation-start-values"],
      "/api/chief/conversation/start-values"
    ],
    [
      "Worker type manifests",
      () => queries.workerTypeManifests(),
      ["worker-types"],
      "/api/worker-types"
    ],
    ["Workers", () => queries.workers(), ["workers"], "/api/workers"],
    ["skills home", () => queries.skillsHome(), ["skills-home"], "/api/skills"]
  ] satisfies Array<
    [
      name: string,
      createQuery: () => QueryDefinition,
      expectedKey: readonly unknown[],
      expectedPath: string
    ]
  >)(
    "%s has the public key and path and forwards cancellation",
    async (_name, createQuery, expectedKey, expectedPath) => {
      const query = createQuery();
      const signal = new AbortController().signal;

      expect(query.queryKey).toEqual(expectedKey);
      await executeQuery(query, signal);

      expect(lastFetch()).toEqual([expectedPath, { signal }]);
    }
  );

  it("keeps a whole Ticket id in the key and URL-encodes it in both Ticket paths", async () => {
    const ticketId = "t_a/b?c";
    const signal = new AbortController().signal;
    const ticket = queries.ticket(ticketId);
    const conversationStartValues = queries.ticketConversationStartValues(ticketId);

    expect(ticket.queryKey).toEqual(["ticket", ticketId]);
    await executeQuery(ticket, signal);
    expect(lastFetch()).toEqual(["/api/tickets/t_a%2Fb%3Fc", { signal }]);

    expect(conversationStartValues.queryKey).toEqual([
      "ticket",
      ticketId,
      "conversation-start-values"
    ]);
    await executeQuery(conversationStartValues, signal);
    expect(lastFetch()).toEqual([
      "/api/tickets/t_a%2Fb%3Fc/conversation/start-values",
      { signal }
    ]);
  });

  it("keeps a whole Worker type in the key and URL-encodes it in the path", async () => {
    const workerType = "coding/a b";
    const signal = new AbortController().signal;
    const worker = queries.worker(workerType);

    expect(worker.queryKey).toEqual(["worker", workerType]);
    await executeQuery(worker, signal);

    expect(lastFetch()).toEqual(["/api/workers/coding%2Fa%20b", { signal }]);
  });

  it("uses equal keys for the same resource and unequal keys for different resources", () => {
    expect(queries.ticket("t_demo").queryKey).toEqual(queries.ticket("t_demo").queryKey);
    expect(queries.ticket("t_demo").queryKey).not.toEqual(queries.ticket("t_other").queryKey);
    expect(queries.worker("coding").queryKey).toEqual(queries.worker("coding").queryKey);
    expect(queries.worker("coding").queryKey).not.toEqual(queries.worker("research").queryKey);
  });

  it("exposes stable shared-resource keys", () => {
    expect(queries.projects().queryKey).toEqual(["projects"]);
    expect(queries.workerTypeManifests().queryKey).toEqual(["worker-types"]);
    expect(queries.skillsHome().queryKey).toEqual(["skills-home"]);
    expect(queries.chiefConversationStartValues().queryKey).toEqual([
      "chief",
      "conversation-start-values"
    ]);
  });
});
