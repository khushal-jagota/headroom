/** Risks: admission must preserve individual versus bulk intent without awaiting
 * unrelated reads; a reopened screen must select persisted progress for its owner;
 * a startup failure after relocation must give a different recovery action.
 * These gaps use the existing fetch/query fixture, without a browser host. */
import { afterEach, expect, it, vi } from "vitest";
import { latestWorkMove, sameWorkMoveOwner, submitWorkMove, workMoveDuration, workMoveItemPhaseText, workMoveItemWaitText, workMoveNextAction, workMoveResultText } from "../src/lib/machines/workMoves";
import { queries } from "../src/lib/queryCatalogue";
import { queryClient } from "../src/lib/queryClient";
import type { WorkMoveOperation, WorkMoveOwner, WorkMoveResult } from "../../server/work_moves/contracts";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const ticket = {kind: "ticket", id: "ticket-one"} as const;
function operation(owner: WorkMoveOwner = ticket, started = 1000, everything = false): WorkMoveOperation {
  return {
    operation_id: `move-${started}`, machine_id: "second", everything,
    started_at_ms: started, finished_at_ms: null,
    items: [{owner, title: "Draft", conversation_id: null, phase: "verifying", phases: [
      {phase: "queued", started_at_ms: started, finished_at_ms: started + 10},
      {phase: "verifying", started_at_ms: started + 10, finished_at_ms: null}
    ], result: null}]
  };
}

it("preserves selected owner and explicit bulk intent, returning acceptance before query refetches", async () => {
  const accepted = operation();
  const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(accepted), {status: 202}));
  vi.stubGlobal("fetch", fetch);
  const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockReturnValue(new Promise(() => {}));
  expect(await submitWorkMove(ticket, "second")).toEqual(accepted);
  expect(String(fetch.mock.calls[0][0])).toBe("/api/work-moves/operations");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({owner: ticket, machine_id: "second"});
  expect(invalidate).toHaveBeenCalledOnce();
  expect(await submitWorkMove(null, "second")).toEqual(accepted);
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({machine_id: "second"});
  expect(queries.workMoveOperations().queryKey).toEqual(["work-move-operations"]);
});

it("recovers the latest owner progress, including its item in a bulk move, from server records", () => {
  const older = operation(ticket, 1000);
  const latest = operation(ticket, 3000, true);
  const unrelated = operation({kind: "manager", id: ticket.id}, 5000);
  const chief = operation({kind: "chief"}, 6000);
  const persisted = [unrelated, older, chief, latest];
  expect(latestWorkMove(persisted, ticket)).toBe(latest);
  expect(latestWorkMove(persisted, {kind: "chief"})).toBe(chief);
  expect(latestWorkMove(persisted, null, "second")).toBe(latest);
  expect(latestWorkMove(persisted, null, "another-machine")).toBeNull();
  expect(latestWorkMove([], ticket)).toBeNull();
  expect(sameWorkMoveOwner(ticket, unrelated.items[0].owner)).toBe(false);
  const initialQueue = {...latest.items[0], phase: "queued" as const};
  const recoveryQueue = {...initialQueue, conversation_id: "destination-conversation"};
  expect(workMoveItemPhaseText(initialQueue)).toBe("Queued");
  expect(workMoveItemWaitText(initialQueue)).toBe("Wait for this move to start.");
  expect(workMoveItemPhaseText(recoveryQueue)).toBe("Recovery prompt queued");
  expect(workMoveItemWaitText(recoveryQueue)).toBe("Wait for the destination machine to accept its recovery prompt.");
  // A current read wins over the original accepted snapshot with the same start.
  const updated = {...latest, finished_at_ms: 4000};
  expect(latestWorkMove([updated, latest], ticket)).toBe(updated);
});

it("retains source refusals and distinguishes a moved conversation that cannot start", () => {
  const base: WorkMoveResult = {owner: {kind: "chief"}, title: "Chief", machine_id: "second", moved: false, error: "Push the work first."};
  expect(workMoveResultText(base)).toBe("Push the work first.");
  expect(workMoveResultText({...base, moved: true, error: "The new conversation did not start."})).toBe("Moved, but The new conversation did not start.");
  const item = {...operation().items[0], phase: "failed" as const, result: base};
  expect(workMoveNextAction(item)).toContain("The destination did not change.");
  expect(workMoveNextAction({...item, result: {...base, moved: true}})).toContain("The destination changed.");
  expect(workMoveNextAction({...item, result: {...base, error: null}})).toBe("");
});

it("keeps completed phase durations precise and active durations in whole seconds", () => {
  expect(workMoveDuration(1000, 2500, 5000)).toBe("1.5s");
  expect(workMoveDuration(1000, null, 5500)).toBe("4s");
  expect(workMoveDuration(1000, null, 65000)).toBe("1m 4s");
});
