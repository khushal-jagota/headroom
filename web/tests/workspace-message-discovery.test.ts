import { describe, expect, it } from "vitest";
import { workspaceMessageConversations, workspaceSettledUnreadCount, workspaceUnreadConversationCount } from "../src/lib/workspaceMessageDiscovery";
import type { BoardCard, BoardSprintItem, ChiefManagementSettings, SprintItemWorkspaceTicket } from "../src/lib/types";

const quiet = { awaiting_reply:false, awaiting_answer:false, awaiting_approval:false,
  awaiting_agent_approval:false, assigned:false, agent_state:"idle" as const };
const card = (id:string, values:Partial<BoardCard> = {}): BoardCard & SprintItemWorkspaceTicket => ({
  ...quiet, id, title:id, priority:"P2", stage:"needs_implementation", ticket_status:"agent",
  conversation_id:`conversation-${id}`, sprint_item_id:null, sprint_item_title:null,
  ...values
} as BoardCard & SprintItemWorkspaceTicket);
const item = (id:string, values:Partial<BoardSprintItem> = {}): BoardSprintItem => ({
  ...quiet, id, title:id, priority:"P2", created_at:1, conversation_id:`supervisor-${id}`,
  ticket_rollup:quiet, tickets:[], ...values
});
const chief = (values:Partial<ChiefManagementSettings> = {}):ChiefManagementSettings => ({
  label:"Chief of Staff", conversation_id:"chief-conversation", awaiting_reply:false, ...values
} as ChiefManagementSettings);

describe("exact unread conversation discovery", () => {
  it("includes own supervisor, Chief and Workers even under settled parents or terminal stages", () => {
    const parent = item("parent", {settled:true, awaiting_reply:true});
    const rows = workspaceMessageConversations([
      card("done", {awaiting_reply:true, stage:"done", sprint_item_id:parent.id}),
      card("cancelled", {awaiting_reply:true, stage:"cancelled", ticket_status:"cancelled", sprint_item_id:parent.id})
    ], [parent], chief({awaiting_reply:true}));
    expect(rows.map((row) => [row.kind,row.id,row.settled])).toEqual([
      ["ticket","done",true], ["ticket","cancelled",true], ["sprint_item","parent",true], ["chief","chief",false]
    ]);
    expect(rows[0].parentTitle).toBe("parent");
    expect(workspaceSettledUnreadCount(rows)).toBe(3);
  });

  it("never invents unread from ask, approval, child rollups, error or incomplete conversation identity", () => {
    const rows = workspaceMessageConversations([
      card("ask", {awaiting_answer:true}), card("approval", {awaiting_approval:true}),
      card("errored", {agent_state:"errored"}), card("missing", {awaiting_reply:true,conversation_id:null})
    ], [item("aggregate", {ticket_rollup:{...quiet,awaiting_reply:true}})], chief());
    expect(rows).toEqual([]);
  });

  it("prefers canonical cards over complete child copies and deduplicates actual conversation IDs", () => {
    const canonical = card("same", {awaiting_reply:true,title:"Canonical",sprint_item_id:"parent"});
    const parent = item("parent", {settled:true, awaiting_reply:true, conversation_id:canonical.conversation_id,
      tickets:[card("same", {awaiting_reply:true,title:"Stale"}),
        card("fallback", {awaiting_reply:true,stage:"done"}),
        card("read", {awaiting_reply:true})]});
    const rows = workspaceMessageConversations([canonical, card("read", {awaiting_reply:false})], [parent]);
    expect(rows.map((row) => row.title)).toEqual(["Canonical","fallback"]);
    expect(workspaceSettledUnreadCount(rows)).toBe(2);
    expect(workspaceSettledUnreadCount([...rows,rows[0]])).toBe(2);
  });

  it("counts a shared conversation once inside Settled even when its primary row is active", () => {
    const active = card("active", {awaiting_reply:true});
    const settled = item("settled", {settled:true, awaiting_reply:true,
      conversation_id:active.conversation_id, tickets:[
        card("alias", {awaiting_reply:true,conversation_id:active.conversation_id})
      ]});
    const rows = workspaceMessageConversations([active], [settled]);
    expect(rows).toHaveLength(1);
    expect(rows[0].settled).toBe(false);
    expect(workspaceSettledUnreadCount(rows, [settled])).toBe(1);
    // A stale child copy cannot bring a read canonical conversation back.
    const canonical = card("alias", {awaiting_reply:false,conversation_id:active.conversation_id});
    expect(workspaceSettledUnreadCount(workspaceMessageConversations([canonical], [{...settled,awaiting_reply:false}]), [settled])).toBe(0);
  });

  it("takes explicit Chief assignment over stray legacy parent and retains quiet context", () => {
    const rows = workspaceMessageConversations([
      card("chief-child", {awaiting_reply:true,sprint_item_id:"stale",orchestrator:{kind:"chief",id:"chief"}}),
      card("explicit-child", {awaiting_reply:true,sprint_item_id:null,orchestrator:{kind:"sprint_item",id:"live"}})
    ], [item("stale",{settled:true}),item("live",{title:"Current parent"})]);
    expect(rows.map((row) => [row.parentId,row.parentTitle,row.settled])).toEqual([
      ["chief","Chief of Staff",false],["live","Current parent",false]
    ]);
  });

  it("counts distinct unread conversations inside any page group and responds to cleared facts", () => {
    const rows = [card("a",{awaiting_reply:true}),card("duplicate",{awaiting_reply:true,conversation_id:"conversation-a"}),
      card("question",{awaiting_answer:true}),card("missing",{awaiting_reply:true,conversation_id:null})];
    expect(workspaceUnreadConversationCount(rows)).toBe(1);
    expect(workspaceUnreadConversationCount(rows.map((row)=>({...row,awaiting_reply:false})))).toBe(0);
    expect(workspaceMessageConversations([],[],null)).toEqual([]);
  });
});
