import { describe, expect, it } from "vitest";
import {
  advanceTargetFor,
  buildLifecycle,
  ceilingOptionsFor,
  fieldIsPassedFor,
  fieldStageVisualStateFor,
  gatingFieldFor,
  lifecycleFor,
  preferredScopeCeilingFor,
  ticketStageVisualStateFor,
  type WorkerTypeManifest,
  type WorkerTypesResponse
} from "../src/lib/lifecycle";
import type { TicketDetail } from "../src/lib/types";
import { atCapLabel, ticketStatusText } from "../src/lib/ui";

const codingManifest = {
  worker_type: "coding",
  label: "Coding",
  stages: [
    {
      id: "needs_kickoff",
      label: "Kickoff",
      gating_field: "kickoff",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "needs_success",
      label: "Success",
      gating_field: "success",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "needs_approach",
      label: "Approach",
      gating_field: "approach",
      is_terminal: false,
      ownership_mode: "user"
    },
    {
      id: "needs_plan",
      label: "Plan",
      gating_field: "plan",
      is_terminal: false,
      ownership_mode: "user"
    },
    {
      id: "needs_implementation",
      label: "Implementation",
      gating_field: "implementation",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "needs_closeout",
      label: "Closeout",
      gating_field: "closeout",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "done",
      label: "Done",
      gating_field: null,
      is_terminal: true,
      ownership_mode: null
    }
  ],
  dropped: {
    id: "dropped",
    label: "Dropped",
    gating_field: null,
    is_terminal: true,
    ownership_mode: null
  },
  advance: {
    needs_kickoff: "needs_success",
    needs_success: "needs_approach",
    needs_approach: "needs_plan",
    needs_plan: "needs_implementation",
    needs_implementation: "needs_closeout",
    needs_closeout: "done"
  },
  fields: [
    { id: "kickoff", label: "Kickoff" },
    { id: "success", label: "Success" },
    { id: "approach", label: "Approach" },
    { id: "plan", label: "Plan" },
    { id: "implementation", label: "Implementation" },
    { id: "closeout", label: "Closeout" }
  ],
  ceiling_range: [
    "needs_success",
    "needs_approach",
    "needs_plan",
    "needs_implementation",
    "needs_closeout",
    "done"
  ],
  default_ceiling: "needs_success",
  worker_profile_id: "panels-worker-coding",
  default_backend: "hermes",
  default_model: null,
  default_reasoning_effort: null
} satisfies WorkerTypeManifest;

const researchManifest = {
  worker_type: "research",
  label: "Research",
  stages: [
    {
      id: "needs_brief",
      label: "Brief",
      gating_field: "brief",
      is_terminal: false,
      ownership_mode: "user"
    },
    {
      id: "needs_findings",
      label: "Findings",
      gating_field: "findings",
      is_terminal: false,
      ownership_mode: "user"
    },
    {
      id: "needs_writeup",
      label: "Writeup",
      gating_field: "writeup",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "done",
      label: "Done",
      gating_field: null,
      is_terminal: true,
      ownership_mode: null
    }
  ],
  dropped: {
    id: "dropped",
    label: "Dropped",
    gating_field: null,
    is_terminal: true,
    ownership_mode: null
  },
  advance: {
    needs_brief: "needs_findings",
    needs_findings: "needs_writeup",
    needs_writeup: "done"
  },
  fields: [
    { id: "brief", label: "Brief" },
    { id: "findings", label: "Findings" },
    { id: "writeup", label: "Writeup" }
  ],
  ceiling_range: ["needs_findings", "needs_writeup", "done"],
  default_ceiling: "needs_findings",
  worker_profile_id: "research-worker",
  default_backend: "claude",
  default_model: "claude-opus",
  default_reasoning_effort: "high"
} satisfies WorkerTypeManifest;

const workerTypesResponse = {
  worker_types: [codingManifest, researchManifest]
} satisfies WorkerTypesResponse;

const codingLifecycle = buildLifecycle(codingManifest);

function ticketDetail(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t_demo",
    title: "Demonstrate lifecycle",
    worker_type: "coding",
    employee_backend: "hermes",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null,
    employee_configuration_editable: true,
    stage: "needs_success",
    ceiling: "done",
    ceiling_holder: { kind: "owner", id: "owner" },
    at_cap: "propose",
    priority: "P1",
    resolved_priority_anchors: {
      sprint_item: null,
      project: null
    },
    conversation_id: null,
    conversation_history: [],
    verdict: null,
    trouble_notes: [],
    field_values: {},
    pending_proposal: null,
    archived_field_content: "",
    guidance: "",
    ...overrides
  };
}

describe("coding lifecycle", () => {
  it("projects fields, Stage order, gating, advance, ownership, and label", () => {
    expect(codingLifecycle.workerType).toBe("coding");
    expect(codingLifecycle.workerTypeLabel).toBe("Coding");
    expect(codingLifecycle.fieldIds).toEqual([
      "kickoff",
      "success",
      "approach",
      "plan",
      "implementation",
      "closeout"
    ]);
    expect(codingLifecycle.stageOrder).toEqual([
      "needs_kickoff",
      "needs_success",
      "needs_approach",
      "needs_plan",
      "needs_implementation",
      "needs_closeout",
      "done"
    ]);
    expect(codingLifecycle.gatingField).toEqual({
      needs_kickoff: "kickoff",
      needs_success: "success",
      needs_approach: "approach",
      needs_plan: "plan",
      needs_implementation: "implementation",
      needs_closeout: "closeout"
    });
    expect(codingLifecycle.gatedStage).toEqual({
      kickoff: "needs_kickoff",
      success: "needs_success",
      approach: "needs_approach",
      plan: "needs_plan",
      implementation: "needs_implementation",
      closeout: "needs_closeout"
    });
    expect(codingLifecycle.advance).toEqual({
      needs_kickoff: "needs_success",
      needs_success: "needs_approach",
      needs_approach: "needs_plan",
      needs_plan: "needs_implementation",
      needs_implementation: "needs_closeout",
      needs_closeout: "done"
    });
    expect(codingLifecycle.stageOwnershipMode).toEqual({
      needs_kickoff: "worker",
      needs_success: "worker",
      needs_approach: "user",
      needs_plan: "user",
      needs_implementation: "worker",
      needs_closeout: "worker",
      done: null
    });
  });

  it("offers scope options from the beginning and middle of the range", () => {
    expect(ceilingOptionsFor(codingLifecycle, "needs_success")).toEqual([
      { value: "needs_success", label: "needs success" },
      { value: "needs_approach", label: "needs approach" },
      { value: "needs_plan", label: "needs plan" },
      { value: "needs_implementation", label: "needs implementation" },
      { value: "needs_closeout", label: "needs closeout" },
      { value: "done", label: "done" }
    ]);
    expect(ceilingOptionsFor(codingLifecycle, "needs_plan")).toEqual([
      { value: "needs_plan", label: "needs plan" },
      { value: "needs_implementation", label: "needs implementation" },
      { value: "needs_closeout", label: "needs closeout" },
      { value: "done", label: "done" }
    ]);
  });

  it("derives the approval ceiling from the stage after the newly entered Stage", () => {
    expect(preferredScopeCeilingFor(codingLifecycle, "needs_success")).toBe("needs_approach");
    expect(preferredScopeCeilingFor(codingLifecycle, "done")).toBe("done");
  });

  it("reads the Ticket's one pending proposal", () => {
    expect(
      fieldStageVisualStateFor(
        codingLifecycle,
        ticketDetail({
          pending_proposal: {
            field: "success",
            body: "Proposed success",
            proposed_by: "worker",
            created_at: 1
          }
        }),
        "success"
      )
    ).toBe("current-awaiting-approval");
  });

  it("formats Ticket status text", () => {
    expect(ticketStatusText("awaiting_approval")).toBe("awaiting approval");
  });

});

describe("fallbacks before lifecycle data is available", () => {
  it("returns the public null defaults", () => {
    expect(gatingFieldFor(null, "needs_success")).toBeNull();
    expect(advanceTargetFor(null, "needs_plan", "done")).toBeNull();
    expect(ceilingOptionsFor(null, "needs_success")).toEqual([]);
    expect(fieldIsPassedFor(null, "success", "needs_approach")).toBe(false);
    expect(
      ticketStageVisualStateFor(null, {
        ticketStage: "needs_success",
        ticketStatus: "agent",
        fieldName: "success"
      })
    ).toBe("upcoming");
  });
});

describe("manifest-driven Worker types", () => {
  it("derives the second Worker's order, gating, advance, scope, passed fields, ownership, and visual state", () => {
    const lifecycle = lifecycleFor(workerTypesResponse, "research");

    expect(lifecycle).not.toBeNull();
    if (!lifecycle) {
      throw new Error("research lifecycle was not found");
    }

    expect(lifecycle.workerType).toBe("research");
    expect(lifecycle.workerTypeLabel).toBe("Research");
    expect(lifecycle.fieldIds).toEqual(["brief", "findings", "writeup"]);
    expect(lifecycle.stageOrder).toEqual([
      "needs_brief",
      "needs_findings",
      "needs_writeup",
      "done"
    ]);
    expect(lifecycle.gatingField).toEqual({
      needs_brief: "brief",
      needs_findings: "findings",
      needs_writeup: "writeup"
    });
    expect(lifecycle.gatedStage).toEqual({
      brief: "needs_brief",
      findings: "needs_findings",
      writeup: "needs_writeup"
    });
    expect(lifecycle.advance).toEqual({
      needs_brief: "needs_findings",
      needs_findings: "needs_writeup",
      needs_writeup: "done"
    });
    expect(lifecycle.stageOwnershipMode).toEqual({
      needs_brief: "user",
      needs_findings: "user",
      needs_writeup: "worker",
      done: null
    });
    expect(gatingFieldFor(lifecycle, "needs_findings")).toBe("findings");
    expect(advanceTargetFor(lifecycle, "needs_brief", "done")).toBe("needs_findings");
    expect(advanceTargetFor(lifecycle, "needs_writeup", "done")).toBe("done");
    expect(ceilingOptionsFor(lifecycle, "needs_findings")).toEqual([
      { value: "needs_findings", label: "needs findings" },
      { value: "needs_writeup", label: "needs writeup" },
      { value: "done", label: "done" }
    ]);
    expect(fieldIsPassedFor(lifecycle, "brief", "needs_findings")).toBe(true);
    expect(fieldIsPassedFor(lifecycle, "findings", "needs_findings")).toBe(false);
    expect(
      ticketStageVisualStateFor(lifecycle, {
        ticketStage: "needs_findings",
        ticketStatus: "agent",
        fieldName: "findings"
      })
    ).toBe("current-running");
  });

  it("looks up both known Worker types and returns null for unknown or unloaded types", () => {
    expect(lifecycleFor(workerTypesResponse, "coding")).toEqual(codingLifecycle);
    expect(lifecycleFor(workerTypesResponse, "research")?.workerType).toBe("research");
    expect(lifecycleFor(workerTypesResponse, "nope")).toBeNull();
    expect(lifecycleFor(undefined, "coding")).toBeNull();
    expect(lifecycleFor(workerTypesResponse, null)).toBeNull();
  });
});
