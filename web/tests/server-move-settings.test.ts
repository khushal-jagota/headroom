import { describe, expect, it } from "vitest";
import { queries } from "../src/lib/queryCatalogue";
import { SERVER_MOVE_POLL_MS } from "../src/lib/machines/config";
import { canMoveServerTo, serverMovePhaseText } from "../src/lib/machines/logic/serverMove";
import type { Machine, ServerMovePhase, ServerMoveResponse, ServerMoveView } from "../src/lib/machines/contracts";

const machine: Machine = { machine_id: "m2", name: "Mac 2", icon: "laptop", color: "jade", is_home: false,
  available: true, running_workers: 0, waiting_tickets: 0 };
const run = (phase: ServerMovePhase, failure: string | null = null): ServerMoveView => ({ move_id: "v1", target_machine_id: "m2",
  server_url: "https://new.example.com", port: 8767, phase, failure, started_at_ms: 1, updated_at_ms: 2 });
const active = ["preparing", "copying", "paused", "committing"] as const;

// While the server pauses, the browser change signal can be held. The row must still move
// through the phases, and no second move may start over a run that is under way.
describe("moving the server in Settings → Machines", () => {
  it("names each phase in the owner's words", () => {
    expect(Object.fromEntries((["preparing", "copying", "paused", "committing", "moved"] as const)
      .map(phase => [phase, serverMovePhaseText(run(phase))]))).toEqual({
      preparing: "Getting the server package",
      copying: "Copying the server's state",
      paused: "Paused: checking the new server",
      committing: "Switching",
      moved: "Moved to https://new.example.com",
    });
    expect(serverMovePhaseText(run("failed", "The new server did not answer."))).toBe("The move failed: The new server did not answer.");
  });

  it("offers the move only to an available machine that is not home, and never during a run", () => {
    expect(canMoveServerTo(machine, null)).toBe(true);
    expect(canMoveServerTo({ ...machine, is_home: true }, null)).toBe(false);
    expect(canMoveServerTo({ ...machine, available: false }, null)).toBe(false);
    for (const phase of active) expect(canMoveServerTo(machine, run(phase))).toBe(false);
    expect(canMoveServerTo(machine, run("failed", "No."))).toBe(true);
  });

  it("polls the run only while it is under way", () => {
    const interval = queries.serverMove().refetchInterval;
    const at = (move: ServerMoveView | null) => interval({ state: { data: { body: { move } satisfies ServerMoveResponse } } });
    for (const phase of active) expect(at(run(phase))).toBe(SERVER_MOVE_POLL_MS);
    expect(at(run("moved"))).toBe(false);
    expect(at(run("failed", "No."))).toBe(false);
    expect(at(null)).toBe(false);
  });
});
