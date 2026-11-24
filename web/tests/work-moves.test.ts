/** Risk: an individual move must never become a bulk request, and partial startup failure
 * must not look like a move refusal. No prior provider or server-move test covers this API.
 * Exercise the production submission boundary without a new browser host. */
import { afterEach, expect, it, vi } from "vitest";
import { submitWorkMove, workMoveResultText } from "../src/lib/machines/workMoves";
import { queries } from "../src/lib/queryCatalogue";
import type { WorkMoveResult } from "../../server/work_moves/contracts";

afterEach(() => vi.unstubAllGlobals());

it("sends only the selected owner, and requires a separate bulk request", async () => {
  const owner = {kind: "ticket", id: "ticket-one"} as const;
  const result: WorkMoveResult = {owner, title: "Draft", machine_id: "second", moved: true, error: null};
  const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(result), {status: 200}));
  vi.stubGlobal("fetch", fetch);
  expect(await submitWorkMove(owner, "second")).toEqual([result]);
  expect(String(fetch.mock.calls[0][0])).toBe("/api/work-moves");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({owner, machine_id: "second"});
  fetch.mockImplementationOnce(async () => new Response(JSON.stringify({results: [result]}), {status: 200}));
  expect(await submitWorkMove(null, "second")).toEqual([result]);
  expect(String(fetch.mock.calls[1][0])).toBe("/api/work-moves/everything");
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({machine_id: "second"});
  expect(queries.workMoves().queryKey).toEqual(["work-moves"]);
});

it("retains source refusals and distinguishes a moved conversation that cannot start", () => {
  const base: WorkMoveResult = {owner: {kind: "chief"}, title: "Chief", machine_id: "second", moved: false, error: "Push the work first."};
  expect(workMoveResultText(base)).toBe("Push the work first.");
  expect(workMoveResultText({...base, moved: true, error: "The new conversation did not start."})).toBe("Moved, but The new conversation did not start.");
});
