/** Risks: admission must preserve individual versus bulk intent without awaiting
 * unrelated reads; a reopened screen must select persisted progress for its owner;
 * the dialog's five rows must follow the server's recorded phases, mark the right failed row,
 * and say "stopped" only when the agent was stopped; its clock must read tenths.
 * These gaps use the existing fetch/query fixture, without a browser host. */
import { afterEach, expect, it, vi } from "vitest";
import { latestWorkMove, sameWorkMoveOwner, submitWorkMove, workMoveDuration, workMoveFailedRow, workMoveNextStep, workMoveRowOfPhase, workMoveRows } from "../src/lib/machines/workMoves";
import { queries } from "../src/lib/queryCatalogue";
import { queryClient } from "../src/lib/queryClient";
import type { WorkMoveItem, WorkMoveOperation, WorkMoveOwner, WorkMovePhase, WorkMoveResult } from "../../server/work_moves/contracts";

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
  // A current read wins over the original accepted snapshot with the same start.
  const updated = {...latest, finished_at_ms: 4000};
  expect(latestWorkMove([updated, latest], ticket)).toBe(updated);
});

const ticketKind = "ticket" as const;
function item(phases: [WorkMovePhase, number, number | null][], result: WorkMoveResult | null = null, conversationId: string | null = null): WorkMoveItem {
  return {owner: ticket, title: "Draft", conversation_id: conversationId, phase: phases.at(-1)![0],
    phases: phases.map(([phase, started, finished]) => ({phase, started_at_ms: started, finished_at_ms: finished})), result};
}
const refusal: WorkMoveResult = {owner: ticket, title: "Draft", machine_id: "second", moved: false, error: "VPS 1 has uncommitted changes."};

it("maps server phases to the five rows, and a queued prompt with a conversation to the last", () => {
  expect(["stopping", "verifying", "recording", "recovering", "starting"].map(phase => workMoveRowOfPhase(phase as WorkMovePhase, null))).toEqual([0, 1, 2, 3, 4]);
  expect(workMoveRowOfPhase("queued", null)).toBe(0);
  expect(workMoveRowOfPhase("queued", "conversation")).toBe(4);
  expect(workMoveRowOfPhase("complete", null)).toBeNull();
});

it("draws row times from recorded phase times and marks the failed row, or none before any phase ran", () => {
  const running = item([["queued", 0, 100], ["stopping", 100, 900], ["verifying", 900, null]]);
  expect(workMoveRows(running, 5000).map(row => [row.state, row.ms])).toEqual([["done", 900], ["active", 4100], ["pending", null], ["pending", null], ["pending", null]]);
  expect(workMoveRows(running, 21000)[1].slow).toBe(true);
  // The refusal of 25 November: stopping then verifying, then failed.
  const refused = item([["queued", 0, 0], ["stopping", 0, 500], ["verifying", 500, 1300], ["failed", 1300, 1300]], refusal);
  expect(workMoveFailedRow(refused)).toBe(1);
  expect(workMoveRows(refused, 9999).map(row => row.state)).toEqual(["done", "failed", "skipped", "skipped", "skipped"]);
  const validation = item([["queued", 0, 0], ["failed", 0, 0]], refusal);
  expect(workMoveFailedRow(validation)).toBeNull();
  expect(workMoveRows(validation, 9999).every(row => row.state === "skipped")).toBe(true);
  // Moved, but the destination did not start: the fifth row failed. A queued prompt counts there.
  const unstarted = item([["queued", 0, 0], ["stopping", 0, 1], ["verifying", 1, 2], ["recording", 2, 3], ["recovering", 3, 4], ["starting", 4, 5], ["queued", 5, 6], ["failed", 6, 6]], {...refusal, moved: true});
  expect(workMoveFailedRow(unstarted)).toBe(4);
});

it("reads the clock in tenths and says stopped only when the agent was stopped", () => {
  expect(workMoveDuration(12340)).toBe("12.3s");
  expect(workMoveDuration(64300)).toBe("1m 04.3s");
  expect(workMoveDuration(-500)).toBe("0.0s");
  const reachedStopping = item([["queued", 0, 0], ["stopping", 0, 1], ["failed", 1, 1]], refusal);
  expect(workMoveNextStep(reachedStopping, ticketKind, "VPS 1")).toBe("Nothing was lost. The agent on VPS 1 is stopped. Fix the problem above, then try again.");
  expect(workMoveNextStep(item([["queued", 0, 0], ["failed", 0, 0]], refusal), ticketKind, "VPS 1")).toBe("Nothing was lost. Fix the problem above, then try again.");
  expect(workMoveNextStep({...reachedStopping, result: {...refusal, moved: true}}, "manager", "VPS 1")).toBe("Nothing was lost. Fix the problem above, then choose “New conversation” on this Manager.");
  expect(workMoveNextStep({...reachedStopping, result: {...refusal, error: null}}, ticketKind, "VPS 1")).toBe("");
});
