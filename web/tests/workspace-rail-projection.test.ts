import { workItemTicketGroupKey } from "../src/lib/workItemPresentation";
import { describe, expect, it } from "vitest";
import { tick } from "svelte";
import type { BoardCard, BoardSprintItem, SprintItemWorkspaceTicket } from "../src/lib/types";
import { buildLifecycle, type WorkerTypeManifest } from "../src/lib/lifecycle";
import { buildWorkspaceRail, refreshWorkspaceRailSignals, stabilizeWorkspaceRail, workspaceWorkerStageSentence, workspaceListRecap } from "../src/lib/workspaceRail";
import { createRailMotionController, type WorkspaceRailPresentation } from "../src/lib/workspaceRailMotion";

const quiet = {
  awaiting_reply: false, awaiting_answer: false, awaiting_approval: false,
  awaiting_agent_approval: false, assigned: false, agent_state: "idle" as const
};
const card = (id: string, values: Partial<BoardCard> = {}): BoardCard & SprintItemWorkspaceTicket => ({
  id, title: id, is_backlogged:false, activity_at: 0, stage: "needs_implementation",
  ticket_status: "agent", worker_type: "coding", sprint_item_id: null,
  day_ids: [], sprint_id: null, sprint_name: null,
  ...quiet, ...values
} as BoardCard & SprintItemWorkspaceTicket);
const item = (id: string, values: Partial<BoardSprintItem> = {}): BoardSprintItem => ({
  id, title: id, recap_preview: "", created_at: 1, tickets: [], conversation_id: null,
  ticket_rollup: quiet, ...quiet, ...values
});

describe("orchestrator rail projection", () => {
  it("keeps active empty and completed orchestrators until explicit Settle", () => {
    const rail = buildWorkspaceRail([], [
      item("empty"), item("complete", { tickets: [card("done", { stage: "done", ticket_status: "empty" })] }),
      item("settled", { settled: true, tickets: [card("question", { awaiting_answer: true })] })
    ]);
    expect(rail.items.map((row) => row.id)).toEqual(["complete", "empty"]);
    expect(rail.items.every((row) => !row.rested && !row.settled)).toBe(true);
    expect(rail.settledItems.map((row) => row.id)).toEqual(["settled"]);
    expect(rail.settledItems[0].groups[0].key).toBe("awaiting_answer");
  });

  it("keeps Chief ownership distinct and retains complete child facts", () => {
    const rail = buildWorkspaceRail([
      card("legacy-chief", { assigned: true }),
      card("explicit-chief", { orchestrator: { kind: "chief", id: "chief" }, awaiting_reply: true }),
      card("ordinary", { orchestrator: { kind: "sprint_item", id: "ordinary" }, sprint_item_id: "ordinary", assigned: true })
    ], [item("ordinary", { tickets: [
      card("complete-child", { awaiting_approval: true }), card("agent-child"),
      card("historical", { stage: "done", ticket_status: "empty" })
    ] })]);
    expect(rail.chiefGroups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["legacy-chief", "explicit-chief"]);
    expect(rail.items[0].groups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["complete-child", "agent-child"]);
    expect(rail.items[0].tickets).toHaveLength(3);
    expect(rail.items[0].conversationSignal).toBeUndefined();
  });

  it("excludes backlog from Chief, active and settled projections without changing child facts", () => {
    const rows = [card("approval", { is_backlogged:true, awaiting_approval:true }),
      card("supervisor", { is_backlogged:true, awaiting_agent_approval:true }),
      card("message", { is_backlogged:true, awaiting_reply:true }),
      card("quiet", { is_backlogged:true }),
      card("done", { is_backlogged:true, stage:"done", ticket_status:"empty" })];
    const rail = buildWorkspaceRail(rows, [item("ordinary", { tickets:rows }), item("settled", { tickets:rows, settled:true })]);
    for (const groups of [rail.chiefGroups, rail.items[0].groups, rail.settledItems[0].groups]) {
      expect(groups).toEqual([]);
    }
    expect(rail.groups.map((group) => group.key)).toEqual(["backlog"]);
    expect(rail.items[0].tickets).toEqual(rows);
    expect(rail.settledItems[0].tickets).toEqual(rows);
    const resumed = rows.map((row) => ({...row, is_backlogged:false}));
    expect(buildWorkspaceRail(resumed).chiefGroups.map((group) => group.key))
      .toEqual(["awaiting_approval", "agent"]);
    expect(rows[0].awaiting_approval).toBe(true);
  });

  it("filters ineligible overlap before grouping and keeps quiet work in the sidebar", () => {
    const rows = [card("working", { conversation_signal:{working:true,message_for_owner:false} }),
      card("closeout", { ticket_status:"empty", waiting_to_closeout:true }),
      card("agent-review", { ticket_status:"empty", awaiting_agent_approval:true }),
      card("idle", { ticket_status:"empty" }),
      ...["done", "backlog", "blocked"].map((id) => card(id, {
        awaiting_approval:true, stage:id === "done" ? "done" : "needs_plan",
        is_backlogged:id === "backlog", blocked:id === "blocked", ticket_status:"agent"
      }))];
    const rail = buildWorkspaceRail(rows);
    expect(rail.chiefGroups.map((group) => [group.label, group.cards.map((row) => row.id)]))
      .toEqual([["Agent", ["working"]], ["Waiting on Consequences", ["closeout"]],
        ["Awaiting an agent's approval", ["agent-review"]], ["Other", ["idle"]]]);
    expect(rail.groups.find((group) => group.key === "empty")?.label).toBe("Empty");
  });

  it("keeps unread independent of rail categories while preserving global classification and original records", () => {
    const rows = [
      card("working", { awaiting_reply:true, conversation_signal:{working:true,message_for_owner:true} }),
      card("quiet", { ticket_status:"empty", awaiting_reply:true, conversation_signal:{working:false,message_for_owner:true} }),
      card("approval", { awaiting_reply:true, awaiting_approval:true }),
      card("answer", { awaiting_reply:true, awaiting_answer:true }),
      card("assigned", { awaiting_reply:true, assigned:true }),
      card("error", { awaiting_reply:true, agent_state:"errored" })
    ];
    const originals = structuredClone(rows);
    const before = buildWorkspaceRail(rows);
    expect(before.chiefGroups.map((group) => [group.key, group.cards.map((row) => row.id)]))
      .toEqual([["awaiting_approval", ["approval"]], ["awaiting_answer", ["answer"]],
        ["assigned", ["assigned"]], ["errored", ["error"]], ["agent", ["working"]], ["empty", ["quiet"]]]);
    for (const row of rows) expect(before.chiefGroups.flatMap((group) => group.cards).find((entry) => entry.id === row.id)).toBe(row);
    expect(rows).toEqual(originals);
    expect(workItemTicketGroupKey(rows[0])).toBe("awaiting_reply");
    expect(before.groups.find((group) => group.key === "awaiting_reply")?.cards.map((row) => row.id))
      .toEqual(["error", "quiet", "working"]);
    const after = stabilizeWorkspaceRail(buildWorkspaceRail(rows.map((row) => ({...row,
      awaiting_reply:false, conversation_signal:row.conversation_signal
        ? {...row.conversation_signal, message_for_owner:false} : undefined }))), before);
    const categories = (rail: typeof before) => rail.chiefGroups.map((group) => [group.key, group.cards.map((row) => row.id)]);
    expect(categories(after)).toEqual(categories(before));
    expect(after.chiefGroups.flatMap((group) => group.cards).every((row) => !row.awaiting_reply && !row.conversation_signal?.message_for_owner)).toBe(true);
    expect(after.chiefGroups.find((group) => group.key === "agent")?.cards[0].conversation_signal?.working).toBe(true);
  });

  it("passes the authoritative orchestrator recap and uses only factual counts when absent", () => {
    const source = item("recap", {recap_preview:"Keep existing summary semantics.", tickets:[card("live"), card("done", {stage:"done"})]});
    const rail = buildWorkspaceRail([], [source]);
    expect(rail.items[0].recapPreview).toBe(source.recap_preview);
    expect(workspaceListRecap(rail.items[0].groups, rail.items[0].recapPreview)).toBe(source.recap_preview);
    expect(workspaceListRecap(rail.items[0].groups, " ")).toBe("1 Ticket in this list.");
    expect(workspaceListRecap(buildWorkspaceRail([card("one"), card("two")]).chiefGroups)).toBe("2 Tickets in this list.");
    expect(workspaceListRecap([])).toBe("Nothing in this list right now.");
  });

  it("retains survivors through refetch and appends entries and re-entries per category", async () => {
    const initial = buildWorkspaceRail([card("a", {activity_at:20}), card("b", {activity_at:10})]);
    const refreshed = stabilizeWorkspaceRail(buildWorkspaceRail([
      card("b", {activity_at:90}), card("a", {activity_at:1}), card("new", {activity_at:100})]), initial);
    expect(refreshed.chiefGroups[0].cards.map((row) => row.id)).toEqual(["a", "b", "new"]);
    const moved = stabilizeWorkspaceRail(buildWorkspaceRail([
      card("a", {awaiting_approval:true}), card("b"), card("new")]), refreshed);
    expect(moved.chiefGroups.map((group) => group.key)).toEqual(["awaiting_approval", "agent"]);
    const reentered = stabilizeWorkspaceRail(buildWorkspaceRail([card("a"), card("b"), card("new")]), moved);
    expect(reentered.chiefGroups[0].cards.map((row) => row.id)).toEqual(["b", "new", "a"]);
    expect(reentered.chiefGroups[0].cards[2].awaiting_approval).toBe(false);
    // Hover defers reassignment and new groups, while the exact conversation stays live.
    const updated = buildWorkspaceRail([], [item("new-parent", { tickets: [
      card("a", {awaiting_approval:true, title:"Changed title", conversation_id:"rebound",
        conversation_signal:{working:true,message_for_owner:false}})
    ] })]);
    const frozen = refreshWorkspaceRailSignals(reentered, updated);
    expect(frozen.chiefGroups[0].cards.map((row) => row.id)).toEqual(["b", "new", "a"]);
    expect(frozen.items).toEqual([]);
    expect(frozen.chiefGroups[0].cards[2]).toEqual({...reentered.chiefGroups[0].cards[2],
      conversation_id:"rebound", conversation_signal:{working:true,message_for_owner:false}});
    // A pointer can arrive after drain captures the answer but before exits settle.
    let presented: WorkspaceRailPresentation = {rail:reentered, settledOpen:false, settledMessages:{}};
    const motion = createRailMotionController(() => null, () => presented, (next) => presented = next);
    motion.update({...presented, rail:updated});
    motion.freeze(true);
    await tick();
    expect(presented.rail).toEqual(frozen);
    motion.freeze(false);
    await tick();
    expect(presented.rail).toEqual(stabilizeWorkspaceRail(updated, frozen));
    motion.dispose();
    // A frozen message can disappear from discovery after it is read or its
    // parent reopens. Membership waits, but facts come from the complete rail.
    const child = card("child", {conversation_id:"old-conversation",
      conversation_signal:{working:true, message_for_owner:true}});
    presented = {rail:buildWorkspaceRail([], [item("settled", {settled:true, tickets:[child]})]),
      settledOpen:true, settledMessages:{settled:[{kind:"ticket", id:"child", title:"Original title",
        parentId:"settled", parentTitle:"Original parent", settled:true,
        conversationId:"old-conversation", conversationSignal:child.conversation_signal}]}};
    const childMotion = createRailMotionController(() => null, () => presented, (next) => presented = next);
    childMotion.freeze(true);
    childMotion.update({rail:buildWorkspaceRail([], [item("reopened", {tickets:[{...child,
      title:"Changed title", conversation_id:"new-conversation",
      conversation_signal:{working:false, message_for_owner:false}}]})]), settledOpen:true, settledMessages:{}});
    expect(presented.settledMessages.settled).toEqual([{kind:"ticket", id:"child", title:"Original title",
      parentId:"settled", parentTitle:"Original parent", settled:true, conversationId:"new-conversation",
      conversationSignal:{working:false, message_for_owner:false}}]);
    childMotion.freeze(false);
    await tick();
    expect(presented.settledMessages).toEqual({});
    childMotion.dispose();
  });

  it("preserves creation and ID order and exact direct-conversation message marks", () => {
    const rail = buildWorkspaceRail([], [
      item("newer", { created_at: 20 }), item("older", { created_at: 10 }),
      item("urgent", { created_at: 0, awaiting_reply: true, conversation_signal: {working:true,message_for_owner:true} }),
      item("children-message", { ticket_rollup: { ...quiet, awaiting_reply: true } })
    ]);
    expect(rail.items.map((row) => row.id)).toEqual(["urgent", "children-message", "older", "newer"]);
    expect(rail.items.map((row) => row.conversationSignal)).toEqual([{working:true,message_for_owner:true}, undefined, undefined, undefined]);
  });

  it("states only manifest stages and facts that prove work", () => {
    const lifecycle = buildLifecycle({
      worker_type: "bespoke", label: "Bespoke", stages: [
        { id: "draft", label: "Draft", gating_field: "draft", is_terminal: false, ownership_mode: "worker" },
        { id: "review", label: "Review", gating_field: "review", is_terminal: false, ownership_mode: "user" },
        { id: "done", label: "Done", gating_field: null, is_terminal: true, ownership_mode: null }
      ], advance: {}, fields: [], ceiling_range: [], default_ceiling: "done",
      worker_profile_id: "bespoke", default_backend: "codex", default_model: null, default_reasoning_effort: null
    } as WorkerTypeManifest);
    const reviewing = card("reviewing", {stage:"review", ticket_status:"agent"});
    expect(workspaceWorkerStageSentence(reviewing, lifecycle)).toBe("At Review.");
    expect(workspaceWorkerStageSentence({...reviewing, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Currently doing Review.");
    expect(workspaceWorkerStageSentence({...reviewing, awaiting_approval:true, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Awaiting your approval at Review.");
    expect(workspaceWorkerStageSentence({...reviewing, agent_state:"errored"}, lifecycle)).toBe("The last turn failed at Review.");
    expect(workspaceWorkerStageSentence({...reviewing, blocked:true, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Blocked at Review.");
    expect(workspaceWorkerStageSentence(reviewing, null)).toBe("");
  });
});
