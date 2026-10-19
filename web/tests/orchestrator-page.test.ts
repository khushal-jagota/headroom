import { describe, expect, it } from "vitest";
import { orchestratorPageGroups } from "../src/lib/orchestratorPage";
import { workspaceUnreadConversationCount } from "../src/lib/workspaceMessageDiscovery";
import type { SprintItemWorkspaceTicket } from "../src/lib/types";
const ticket = (id:string, overrides:Partial<SprintItemWorkspaceTicket> = {}): SprintItemWorkspaceTicket => ({
  id, title:id, recap:"",recap_preview:"", is_backlogged:false, stage:"needs_implementation", activity_at:1,
  ticket_status:"agent", waiting_to_closeout:false, has_pending_proposal:false,
  gating_field:null, blocked:false, worker_type:"coding", day_ids:[], sprint_id:null,
  sprint_name:null, awaiting_reply:false, awaiting_answer:false, awaiting_approval:false,
  awaiting_agent_approval:false, assigned:false, agent_state:"idle", conversation_id:`conversation-${id}`,
  ...overrides
});
describe("complete orchestrator page groups", () => {
  it("classifies unread Workers by their work while preserving original dot and distinct count facts", () => {
    const rows = [ticket("live",{awaiting_reply:true}), ticket("done",{stage:"done",ticket_status:"",awaiting_reply:true}),
      ticket("alias",{awaiting_reply:true,conversation_id:"conversation-live"})];
    const groups = orchestratorPageGroups(rows);
    expect(groups.map((group)=>group.key)).toEqual(["agent","done"]);
    expect(groups[0].tickets[1]).toBe(rows[0]);
    expect(workspaceUnreadConversationCount(groups[0].tickets)).toBe(1);
    expect(groups[1].tickets[0].awaiting_reply).toBe(true);
    expect(orchestratorPageGroups(rows.map((row)=>({...row,awaiting_reply:false}))).map((group)=>group.key)).toEqual(["agent","done"]);
  });
  it("uses normal Backlog rows for approval, supervisor review and terminal history", () => {
    const rows = [ticket("owner", { is_backlogged:true, awaiting_approval:true, has_pending_proposal:true }),
      ticket("supervisor", { is_backlogged:true, awaiting_agent_approval:true, has_pending_proposal:true }),
      ticket("done", { is_backlogged:true, stage:"done", ticket_status:"empty" })];
    const groups = orchestratorPageGroups(rows);
    expect(groups.map((group) => [group.key, group.quiet])).toEqual([["backlog",false]]);
    expect(groups[0].tickets).toHaveLength(3);
    for (const row of rows) expect(groups[0].tickets).toContain(row);
    expect(groups[0].tickets.find((row) => row.id === "owner")?.has_pending_proposal).toBe(true);
    expect(groups[0].tickets.find((row) => row.id === "supervisor")?.awaiting_agent_approval).toBe(true);
  });
  it("retains canonical group order, exceptional rows and completion without changing input", () => {
    const rows = [ticket("done",{stage:"done",ticket_status:"",awaiting_reply:true}),
      ticket("blocked",{ticket_status:"blocked"}), ticket("error",{agent_state:"errored"}),
      ticket("answer",{awaiting_answer:true}), ticket("approval",{awaiting_approval:true}),
      ticket("owner",{assigned:true}),ticket("empty",{ticket_status:""})];
    const groups = orchestratorPageGroups(rows);
    expect(groups.map((group)=>group.key)).toEqual(["awaiting_approval","awaiting_answer","assigned","errored","empty","blocked","done"]);
    expect(groups.flatMap((group)=>group.tickets)).toHaveLength(rows.length);
    expect(groups.at(-1)?.label).toBe("Done");
    expect(rows[0].awaiting_reply).toBe(true);
    expect(groups.at(-1)?.tickets[0]).toBe(rows[0]);
  });
});
