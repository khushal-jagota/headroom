import { describe, expect, it } from "vitest";
import { orchestratorPageGroups } from "../src/lib/orchestratorPage";
import { workspaceUnreadConversationCount } from "../src/lib/workspaceMessageDiscovery";
import type { SprintItemWorkspaceTicket } from "../src/lib/types";
const ticket = (id:string, overrides:Partial<SprintItemWorkspaceTicket> = {}): SprintItemWorkspaceTicket => ({
  id, title:id, stage:"needs_implementation", priority:"P2", activity_at:1,
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
  it("retains canonical group order, exceptional rows and truthful cancellation without changing input", () => {
    const rows = [ticket("cancelled",{stage:"cancelled",ticket_status:"",awaiting_reply:true}),
      ticket("blocked",{ticket_status:"blocked"}), ticket("error",{agent_state:"errored"}),
      ticket("answer",{awaiting_answer:true}), ticket("approval",{awaiting_approval:true}),
      ticket("owner",{assigned:true}),ticket("empty",{ticket_status:""})];
    const groups = orchestratorPageGroups(rows);
    expect(groups.map((group)=>group.key)).toEqual(["awaiting_approval","awaiting_answer","assigned","errored","empty","blocked","cancelled"]);
    expect(groups.flatMap((group)=>group.tickets)).toHaveLength(rows.length);
    expect(groups.at(-1)?.label).toBe("Cancelled");
    expect(rows[0].awaiting_reply).toBe(true);
    expect(groups.at(-1)?.tickets[0]).toBe(rows[0]);
  });
});
