import { describe, expect, it } from "vitest";
import { filterOrchestratorChildren } from "../src/lib/orchestratorChildren";
import { orchestratorPageGroups } from "../src/lib/orchestratorPage";
import type { OrchestratorChildFilter, SprintItemWorkspaceTicket } from "../src/lib/types";

const child = (id: string, stage: string, is_backlogged = false): SprintItemWorkspaceTicket => ({
  id, title:id, stage, is_backlogged, priority:"P2", activity_at:1, ticket_status:"empty",
  waiting_to_closeout:false, has_pending_proposal:false, gating_field:null, blocked:false,
  worker_type:"coding", day_ids:[], sprint_id:null, sprint_name:null, awaiting_reply:false,
  awaiting_answer:false, awaiting_approval:false, awaiting_agent_approval:false, assigned:false, agent_state:"idle"
});

describe("orchestrator child discovery", () => {
  it("retains quiet active children and exposes deferred, completed and retired children separately", () => {
    const rows = [child("quiet", "needs_implementation"), child("deferred", "needs_brief", true),
      child("done", "done"), child("retired", "cancelled")];
    const expected: Record<OrchestratorChildFilter, string[]> = {
      active:["quiet", "deferred"], backlogged:["deferred"], done:["done"], retired:["retired"]
    };
    for (const filter of Object.keys(expected) as OrchestratorChildFilter[]) {
      const filtered = filterOrchestratorChildren(rows, filter);
      expect(filtered.map((row) => row.id)).toEqual(expected[filter]);
      expect(orchestratorPageGroups(filtered).flatMap((group) => group.tickets)).toHaveLength(filtered.length);
    }
    expect(orchestratorPageGroups(filterOrchestratorChildren(rows,"retired"))[0].label).toBe("Cancelled");
    expect(filterOrchestratorChildren([],"active")).toEqual([]);
    expect(rows).toHaveLength(4);
  });
});
