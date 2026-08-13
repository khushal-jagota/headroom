import { describe, expect, it } from "vitest";
import { atlasWorld } from "../src/atlas/projection";
import type { AtlasInputs } from "../src/atlas/contracts";
import type { BoardCard } from "../src/lib/types";

function card(values: Partial<BoardCard> = {}): BoardCard {
  return {
    id: "t-1", title: "Make it real", priority: "P1", deadline: null,
    project_id: "p-first", project: "First Works", group_project_id: "p-first",
    group_project: "First Works", activity_at: 1, has_pending_proposal: false,
    ticket_status: "agent", backend_error: null, worker_type: "coding",
    employee_backend: "codex", stage: "build", stage_label: "Build",
    gating_field: null, gating_field_label: null, is_done: false, is_dropped: false,
    blocked: false, conversation_id: null, waiting_to_closeout: false, sprint_item_id: null,
    sprint_item_title: null, sprint_item_priority: null, agent_working: true, needs_me: false,
    latest_turn_ended_sequence: 0, ...values
  };
}

function inputs(): AtlasInputs {
  return {
    projects: [
      { id: "p-first", name: "First Works", summary: "A real project.", priority: "P1", created_at: 1, updated_at: 1 },
      { id: "p-quiet", name: "Quiet Plot", summary: "", priority: null, created_at: 1, updated_at: 1 }
    ],
    board: { columns: [{ stage: "build", cards: [card()] }] },
    workers: { workers: [{ worker_type: "coding", label: "Builder", specialist_skill_name: "panels-worker", suggested_next_ceiling: "implementation", stage_ownership_defaults: {}, launch_defaults: { employee_backend: "codex", employee_launch_model: null, employee_launch_reasoning_effort: null } }], chief_of_staff: { employee_id: "chief", label: "Nightshift", skill: { name: "chief", description: "", markdown_body: "" }, launch_defaults: { employee_backend: "codex", employee_launch_model: null, employee_launch_reasoning_effort: null }, conversation_id: null, needs_me: false, agent_working: false, latest_turn_ended_sequence: 0 } },
    review: { items: [{ review_item_type: "proposal", ticket_id: "t-1", field: "plan", title: "Make it real", waiting_since: 1 }], running_worker_count: 1 }
  };
}

describe("Atlas projection", () => {
  it("keeps project locations stable and routes a building to its live ticket", () => {
    const first = atlasWorld(inputs());
    const second = atlasWorld(inputs());
    expect(first.buildings).toEqual(second.buildings);
    expect(first.buildings[0]).toMatchObject({ id: "p-first", activeTicketId: "t-1", href: "#/workspace/t-1", state: "active" });
    expect(first.buildings[1]).toMatchObject({ id: "p-quiet", href: "#/backlog", state: "quiet" });
  });

  it("uses only existing Panels destinations and stays truthful when nothing exists", () => {
    const world = atlasWorld({ ...inputs(), projects: [], board: { columns: [] }, review: { items: [], running_worker_count: 0 } });
    expect(world.buildings).toEqual([]);
    expect(world.agents.map((agent) => agent.href)).toEqual(["#/workspace/chief-of-staff", "#/config/workers/coding"]);
    expect(world.alerts).toEqual([]);
    expect(world.activeJobCount).toBe(0);
  });
});
