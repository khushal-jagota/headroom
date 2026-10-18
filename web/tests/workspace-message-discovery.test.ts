import { describe, expect, it } from "vitest";
import { workspaceChildMessageConversations, workspaceMessageConversations, workspaceSettledUnreadCount, workspaceUnreadConversationCount } from "../src/lib/workspaceMessageDiscovery";
import { buildWorkspaceRail, workspaceRailChildMessageConversations } from "../src/lib/workspaceRail";
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
      card("unfinished", {awaiting_reply:true, stage:"needs_implementation", ticket_status:"agent", sprint_item_id:parent.id})
    ], [parent], chief({awaiting_reply:true}));
    expect(rows.map((row) => [row.kind,row.id,row.settled])).toEqual([
      ["ticket","done",true], ["ticket","unfinished",true], ["sprint_item","parent",true], ["chief","chief",false]
    ]);
    expect(rows[0].parentTitle).toBe("parent");
    expect(workspaceSettledUnreadCount(rows)).toBe(3);
  });

  it("discovers backlogged messages independently of their single presentation group", () => {
    const rows = [card("owner", { is_backlogged:true, awaiting_reply:true, awaiting_approval:true }),
      card("supervisor", { is_backlogged:true, awaiting_reply:true, awaiting_agent_approval:true }),
      card("done", { is_backlogged:true, awaiting_reply:true, stage:"done" }),
      card("unfinished", { is_backlogged:true, awaiting_reply:true, stage:"needs_implementation", ticket_status:"agent" })];
    const parent = item("ordinary", { tickets:rows });
    expect(buildWorkspaceRail(rows, [parent]).items[0].groups.map((group) => group.key)).toEqual([]);
    expect(workspaceMessageConversations(rows, [parent]).map((row) => row.id))
      .toEqual(["owner", "supervisor", "done", "unfinished"]);
    expect(workspaceUnreadConversationCount(rows)).toBe(4);
    expect(workspaceMessageConversations(rows.map((row) => ({ ...row, awaiting_reply:false })), []))
      .toEqual([]);
  });

  it("excludes backlog from settled sidebar Messages while preserving global discovery", () => {
    const saved = card("saved", {is_backlogged:true, awaiting_reply:true, sprint_item_id:"parent"});
    const fallback = card("fallback", {is_backlogged:true, awaiting_reply:true});
    const ordinary = card("ordinary", {awaiting_reply:true, sprint_item_id:"parent"});
    const parent = item("parent", {settled:true, tickets:[{...saved,is_backlogged:false},fallback,ordinary]});
    const cards = [saved,ordinary];
    const rows = workspaceMessageConversations(cards,[parent]);
    expect(rows.map((row) => row.id)).toEqual(["saved","ordinary","fallback"]);
    expect(workspaceChildMessageConversations(rows,parent,cards).map((row) => row.id))
      .toEqual(["saved","fallback","ordinary"]);
    expect(workspaceRailChildMessageConversations(rows,parent,cards).map((row) => row.id)).toEqual(["ordinary"]);
    const noChild = {...parent,tickets:[]};
    expect(workspaceRailChildMessageConversations(rows,noChild,cards).map((row) => row.id))
      .toEqual(["ordinary","fallback"]);
    // A canonical resume wins over a stale backlogged child, including fallback index rows.
    const resumed = {...saved,is_backlogged:false};
    expect(workspaceRailChildMessageConversations(rows,{...parent,tickets:[saved,fallback]},[resumed])
      .map((row) => row.id)).toEqual(["saved","ordinary"]);
    // A same-conversation alias cannot reintroduce a backlogged child.
    const alias = {...fallback,conversation_id:ordinary.conversation_id};
    expect(workspaceRailChildMessageConversations(rows,{...parent,tickets:[alias,ordinary]},cards)
      .map((row) => row.id)).toEqual(["ordinary"]);
  });

  it("retains an eligible alias when backlog is the global unread representative", () => {
    const saved = card("saved", {is_backlogged:true, awaiting_reply:true, sprint_item_id:"parent",conversation_id:"shared"});
    const ordinary = card("ordinary", {awaiting_reply:true, sprint_item_id:"parent",conversation_id:"shared"});
    const parent = item("parent", {settled:true,tickets:[saved,ordinary]});
    const rows = workspaceMessageConversations([saved,ordinary],[parent]);
    expect(rows.map((row) => row.id)).toEqual(["saved"]);
    expect(workspaceRailChildMessageConversations(rows,parent,[saved,ordinary]).map((row) => row.id))
      .toEqual(["ordinary"]);
    expect(rows.map((row) => row.id)).toEqual(["saved"]);
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
    const aliases = workspaceChildMessageConversations(rows,settled,[active]);
    expect(aliases.map((row)=>[row.id,row.conversationId,row.parentId])).toEqual([["alias",active.conversation_id,"settled"]]);
    const quietSettled = {...settled,awaiting_reply:false};
    expect(workspaceChildMessageConversations(workspaceMessageConversations([active],[quietSettled]),quietSettled,[active])).toHaveLength(1);

    // A stale child copy cannot bring a read canonical conversation back.
    const canonical = card("alias", {awaiting_reply:false,conversation_id:active.conversation_id});
    expect(workspaceSettledUnreadCount(workspaceMessageConversations([canonical], [{...settled,awaiting_reply:false}]), [settled])).toBe(0);
    expect(workspaceChildMessageConversations(workspaceMessageConversations([canonical],[{...settled,awaiting_reply:false}]),settled,[canonical])).toEqual([]);

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
  it("nests exact child conversations beneath their parent without supervisor or Chief rows", () => {
    const parent = item("settled", {settled:true,awaiting_reply:true,tickets:[
      card("done",{stage:"done",ticket_status:"empty",awaiting_reply:true}),
      card("unfinished",{stage:"needs_implementation",ticket_status:"empty",awaiting_reply:true})]});
    const rows = workspaceMessageConversations([], [parent], chief({awaiting_reply:true}));
    expect(workspaceChildMessageConversations(rows,parent).map((row)=>row.id)).toEqual(["done","unfinished"]);
    expect(workspaceSettledUnreadCount(rows,[parent])).toBe(3);
    expect(workspaceChildMessageConversations(rows,item("other"))).toEqual([]);
    const read = {...parent,awaiting_reply:false,tickets:parent.tickets.map((ticket)=>({...ticket,awaiting_reply:false}))};
    expect(workspaceChildMessageConversations(workspaceMessageConversations([], [read]),read)).toEqual([]);
  });

  it("keeps active and Chief terminal and unfinished unread access in their existing nested Messages", () => {
    const activeTickets = [card("active-done",{stage:"done",ticket_status:"empty",awaiting_reply:true,orchestrator:{kind:"sprint_item",id:"active"}}),
      card("active-unfinished",{stage:"needs_implementation",ticket_status:"empty",awaiting_reply:true,orchestrator:{kind:"sprint_item",id:"active"}})];
    const chiefTickets = [card("chief-done",{stage:"done",ticket_status:"empty",awaiting_reply:true,orchestrator:{kind:"chief",id:"chief"}}),
      card("chief-unfinished",{stage:"needs_implementation",ticket_status:"empty",awaiting_reply:true,orchestrator:{kind:"chief",id:"chief"}})];
    const rail = buildWorkspaceRail([...activeTickets,...chiefTickets],[item("active",{tickets:activeTickets})]);
    expect(rail.items[0].groups.find((group)=>group.key==="awaiting_reply")?.cards.map((row)=>row.id)).toEqual(["active-done","active-unfinished"]);
    expect(rail.chiefGroups.find((group)=>group.key==="awaiting_reply")?.cards.map((row)=>row.id)).toEqual(["chief-done","chief-unfinished"]);
  });

  it("keeps explicit Chief assignment above a stale same-Ticket parent while retaining different Ticket aliases", () => {
    const canonical = card("moved",{awaiting_reply:true,orchestrator:{kind:"chief",id:"chief"},conversation_id:"shared"});
    const parent = item("old",{settled:true,tickets:[card("moved",{awaiting_reply:true,conversation_id:"shared"}),card("alias",{awaiting_reply:true,conversation_id:"shared"})]});
    const rows = workspaceMessageConversations([canonical],[parent]);
    expect(workspaceChildMessageConversations(rows,parent,[canonical]).map((row)=>row.id)).toEqual(["alias"]);
  });

});
