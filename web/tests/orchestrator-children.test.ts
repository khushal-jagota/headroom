import { describe, expect, it } from "vitest";
import { filterOrchestratorChildren } from "../src/lib/orchestratorChildren";
import { orchestratorPageGroups } from "../src/lib/orchestratorPage";
import type { OrchestratorChildFilter, SprintItemWorkspaceTicket } from "../src/lib/types";

const child = (id: string, stage: string, is_backlogged = false): SprintItemWorkspaceTicket => ({
  id, title:id, recap_preview:"", stage, is_backlogged, activity_at:1, ticket_status:"empty",
  waiting_to_closeout:false, has_pending_proposal:false, gating_field:null, blocked:false,
  worker_type:"coding", day_ids:[], sprint_id:null, sprint_name:null, awaiting_reply:false,
  awaiting_answer:false, awaiting_approval:false, awaiting_agent_approval:false, assigned:false, agent_state:"idle"
});

describe("orchestrator child discovery", () => {
  it("puts a completed backlogged child only in Done even with pending attention", () => {
    const rows = [child("active", "needs_implementation"), child("backlog", "needs_brief", true),
      {...child("done", "done", true), awaiting_approval:true, awaiting_reply:true}];
    const expected: Record<OrchestratorChildFilter, string[]> = {
      active:["active"], backlogged:["backlog"], done:["done"]
    };
    for (const filter of Object.keys(expected) as OrchestratorChildFilter[]) {
      expect(filterOrchestratorChildren(rows, filter).map(row => row.id)).toEqual(expected[filter]);
    }
  });

  it("retains quiet active children and exposes deferred and completed children separately", () => {
    const rows = [child("quiet", "needs_implementation"), child("deferred", "needs_brief", true),
      child("done", "done")];
    const expected: Record<OrchestratorChildFilter, string[]> = {
      active:["quiet"], backlogged:["deferred"], done:["done"]
    };
    for (const filter of Object.keys(expected) as OrchestratorChildFilter[]) {
      const filtered = filterOrchestratorChildren(rows, filter);
      expect(filtered.map((row) => row.id)).toEqual(expected[filter]);
      expect(orchestratorPageGroups(filtered).flatMap((group) => group.tickets)).toHaveLength(filtered.length);
    }
    const consequences = { ...child("closeout", "needs_consequences"), waiting_to_closeout:true };
    expect(orchestratorPageGroups([consequences])[0].key).toBe("waiting_to_closeout");
    expect(filterOrchestratorChildren([],"active")).toEqual([]);
    expect(rows).toHaveLength(3);
  });
});
