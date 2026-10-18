import { describe, expect, it } from "vitest";
import { chiefChildrenPage, filterOrchestratorChildren } from "../src/lib/orchestratorChildren";
import { orchestratorPageGroups } from "../src/lib/orchestratorPage";
import type { OrchestratorChildFilter, SprintItemWorkspaceTicket } from "../src/lib/types";

const child = (id: string, stage: string, is_backlogged = false): SprintItemWorkspaceTicket => ({
  id, title:id, recap_preview:"", stage, is_backlogged, priority:"P2", activity_at:1, ticket_status:"empty",
  waiting_to_closeout:false, has_pending_proposal:false, gating_field:null, blocked:false,
  worker_type:"coding", day_ids:[], sprint_id:null, sprint_name:null, awaiting_reply:false,
  awaiting_answer:false, awaiting_approval:false, awaiting_agent_approval:false, assigned:false, agent_state:"idle"
});

describe("orchestrator child discovery", () => {
  it("preserves Chief canonical terminal filtering and creation-page membership before groups",()=>{
    const rows=Array.from({length:31},(_,i)=>({...child(`child-${String(i).padStart(2,'0')}`,"needs_implementation"),
      created_at:Math.floor(i/2),terminal:false,activity_at:100-i}));
    rows.reverse();
    const first=chiefChildrenPage(rows,"active",0);
    expect(first.tickets.map(row=>row.id)).toEqual(Array.from({length:30},(_,i)=>`child-${String(i).padStart(2,'0')}`));
    expect(first.page).toEqual({match_count:31,return_count:30,limit:30,offset:0,omitted_before:0,omitted_after:1,complete:false,next_offset:30});
    expect(chiefChildrenPage(rows,"active",30).tickets.map(row=>row.id)).toEqual(['child-30']);
    expect(chiefChildrenPage([],"active",30).page).toMatchObject({omitted_before:0,omitted_after:0,complete:true});
    const terminal={...rows[0],id:'custom-terminal',stage:'cancelled',terminal:true,is_backlogged:true};
    const done={...terminal,id:'done',stage:'done'};
    expect(chiefChildrenPage([terminal,done],"active",0).tickets).toEqual([]);
    expect(chiefChildrenPage([terminal,done],"backlogged",0).tickets).toEqual([]);
    expect(chiefChildrenPage([terminal,done],"done",0).tickets.map(row=>row.id)).toEqual(['done']);
    expect(filterOrchestratorChildren([terminal,done],"active").map(row=>row.id)).toEqual(['custom-terminal']);
    const activityOrder=first.tickets.map((row,i)=>({...row,activity_at:i}));
    expect(orchestratorPageGroups(activityOrder).flatMap(group=>group.tickets).map(row=>row.id)).toEqual(activityOrder.map(row=>row.id).reverse());
  });

  it("keeps terminal backlog in existing history filters without widening active discovery", () => {
    const rows = [child("active", "needs_implementation", true), child("done", "done", true)];
    const expected: Record<OrchestratorChildFilter, string[]> = {
      active:["active"], backlogged:["active"], done:["done"]
    };
    for (const filter of Object.keys(expected) as OrchestratorChildFilter[]) {
      const groups = orchestratorPageGroups(filterOrchestratorChildren(rows, filter));
      expect(groups.map((group) => group.key)).toEqual(["backlog"]);
      expect(groups.flatMap((group) => group.tickets.map((row) => row.id))).toEqual(expected[filter]);
    }
  });

  it("retains quiet active children and exposes deferred and completed children separately", () => {
    const rows = [child("quiet", "needs_implementation"), child("deferred", "needs_brief", true),
      child("done", "done")];
    const expected: Record<OrchestratorChildFilter, string[]> = {
      active:["quiet", "deferred"], backlogged:["deferred"], done:["done"]
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
