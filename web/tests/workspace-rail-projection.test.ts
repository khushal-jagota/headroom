import { describe, expect, it } from "vitest";
import type { BoardCard, BoardSprintItem, SprintItemWorkspaceTicket } from "../src/lib/types";
import { buildLifecycle, type WorkerTypeManifest } from "../src/lib/lifecycle";
import { buildWorkspaceRail, workspaceWorkerStages } from "../src/lib/workspaceRail";

const quiet = {
  awaiting_reply: false, awaiting_answer: false, awaiting_approval: false,
  awaiting_agent_approval: false, assigned: false, agent_state: "idle" as const
};
const card = (id: string, values: Partial<BoardCard> = {}): BoardCard & SprintItemWorkspaceTicket => ({
  id, title: id, is_backlogged:false, priority: "P2", activity_at: 0, stage: "needs_implementation",
  ticket_status: "agent", worker_type: "coding", sprint_item_id: null,
  day_ids: [], sprint_id: null, sprint_name: null,
  ...quiet, ...values
} as BoardCard & SprintItemWorkspaceTicket);
const item = (id: string, values: Partial<BoardSprintItem> = {}): BoardSprintItem => ({
  id, title: id, priority: "P2", created_at: 1, tickets: [], conversation_id: null,
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

  it("keeps Chief ownership distinct and uses complete children in hover", () => {
    const rail = buildWorkspaceRail([
      card("legacy-chief", { assigned: true }),
      card("explicit-chief", { orchestrator: { kind: "chief", id: "chief" }, awaiting_reply: true }),
      card("ordinary", { orchestrator: { kind: "sprint_item", id: "ordinary" }, sprint_item_id: "ordinary", assigned: true })
    ], [item("ordinary", { tickets: [
      card("complete-child", { awaiting_approval: true }), card("agent-child"),
      card("historical", { stage: "done", ticket_status: "empty" }),
      card("obsolete", { ticket_status: "cancelled", awaiting_approval: true })
    ] })]);
    expect(rail.chiefGroups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["legacy-chief", "explicit-chief"]);
    expect(rail.items[0].groups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["complete-child"]);
    expect(rail.items[0].hoverGroups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["complete-child", "agent-child"]);
    expect(rail.items[0].tickets).toHaveLength(4);
    expect(rail.items[0].conversationSignal).toBeUndefined();
  });

  it("preserves priority order and exact direct-conversation message marks", () => {
    const rail = buildWorkspaceRail([], [
      item("newer", { created_at: 20 }), item("older", { created_at: 10 }),
      item("urgent", { priority: "P0", awaiting_reply: true, conversation_signal: {working:true,message_for_owner:true} }),
      item("children-message", { ticket_rollup: { ...quiet, awaiting_reply: true } })
    ]);
    expect(rail.items.map((row) => row.id)).toEqual(["urgent", "children-message", "older", "newer"]);
    expect(rail.items.map((row) => row.conversationSignal)).toEqual([{working:true,message_for_owner:true}, undefined, undefined, undefined]);
  });

  it("derives stages from the served Worker manifest without a fabricated fallback", () => {
    const lifecycle = buildLifecycle({
      worker_type: "bespoke", label: "Bespoke", stages: [
        { id: "draft", label: "Draft", gating_field: "draft", is_terminal: false, ownership_mode: "worker" },
        { id: "review", label: "Review", gating_field: "review", is_terminal: false, ownership_mode: "user" },
        { id: "done", label: "Done", gating_field: null, is_terminal: true, ownership_mode: null }
      ], advance: {}, fields: [], ceiling_range: [], default_ceiling: "done",
      worker_profile_id: "bespoke", default_backend: "codex", default_model: null, default_reasoning_effort: null
    } as WorkerTypeManifest);
    expect(workspaceWorkerStages({ stage: "review" }, lifecycle)).toEqual([
      { id: "draft", label: "Draft", state: "done" },
      { id: "review", label: "Review", state: "now" },
      { id: "done", label: "Done", state: "todo" }
    ]);
    expect(workspaceWorkerStages({ stage: "unknown" }, lifecycle).every((stage) => stage.state === "todo")).toBe(true);
    expect(workspaceWorkerStages({ stage: "review" }, null)).toEqual([]);
  });
});
