import { describe, expect, it } from "vitest";
import {
  failedWorkspaceDeliveries,
  remainingWorkspaceTicketGroups,
  todayWorkspaceTicketGroups,
  workspaceProgress
} from "../src/lib/sprintItemWorkspace";
import { previewHashHref, resolvePreview, sprintItemFileTarget } from "../src/lib/filePreview";
import type { SprintItemWorkspace } from "../src/lib/types";

function workspace(): SprintItemWorkspace {
  return {
    id: "si_workspace",
    title: "Outcome",
    body: "Shared brief",
    priority: "P2",
    deadline: null,
    project_id: "project_panels",
    project: "Panels",
    sprint_id: "sp_current",
    kind: "normal",
    status: "in_progress",
    rollup: {},
    planning_day_id: "day_2026-08-12",
    today_ticket_ids: ["t_review", "t_done"],
    supervisor: {
      agent_key: "sprint_item_supervisor_si_workspace",
      conversation_id: null,
      launch_configuration: {
        employee_backend: "codex",
        employee_launch_model: "gpt-5.6-sol",
        employee_launch_reasoning_effort: "medium"
      }
    },
    tickets: [
      {
        id: "t_review",
        title: "Review this",
        stage: "needs_implementation",
        priority: "P1",
        ticket_status: "awaiting_agent_review",
        waiting_to_closeout: false,
        has_pending_proposal: true,
        proposal_review_route: "agent_review",
        review_route: "agent_review",
        worker_type: "coding",
        day_ids: ["day_2026-08-12"]
      },
      {
        id: "t_done",
        title: "Already done",
        stage: "done",
        priority: "P3",
        ticket_status: "empty",
        waiting_to_closeout: false,
        has_pending_proposal: false,
        proposal_review_route: null,
        review_route: "stop",
        worker_type: "coding",
        day_ids: ["day_2026-08-12"]
      },
      {
        id: "t_later",
        title: "Later",
        stage: "needs_success",
        priority: "P2",
        ticket_status: "agent",
        waiting_to_closeout: false,
        has_pending_proposal: false,
        proposal_review_route: null,
        review_route: "stop",
        worker_type: "coding",
        day_ids: []
      }
    ],
    artifacts: ["proof.md"],
    obligations: [
      {
        id: "so_failed",
        ticket_id: "t_review",
        kind: "agent_review",
        lifecycle: "failed",
        attempt_count: 2,
        retry_at: null,
        last_error: "offline"
      }
    ],
    conversation_history: []
  };
}

describe("Sprint Item workspace presentation", () => {
  it("splits sections on Day membership alone and groups both the same way", () => {
    const value = workspace();
    const shape = (groups: ReturnType<typeof todayWorkspaceTicketGroups>) =>
      groups.map((group) => [group.label, group.tickets.map((ticket) => ticket.id)]);
    // A Ticket finished today stays under Today, in Today's own Done group.
    expect(shape(todayWorkspaceTicketGroups(value))).toEqual([
      ["Awaiting approval", ["t_review"]],
      ["Done", ["t_done"]]
    ]);
    expect(shape(remainingWorkspaceTicketGroups(value))).toEqual([["Agent", ["t_later"]]]);
    expect(workspaceProgress(value)).toBe("1 of 3 done");
    expect(failedWorkspaceDeliveries(value).map((obligation) => obligation.id)).toEqual([
      "so_failed"
    ]);
  });

  it("labels a not-yet-started Ticket with the word used everywhere else", () => {
    const value = workspace();
    const upcoming = { ...value.tickets[0], id: "t_upcoming", ticket_status: "empty", has_pending_proposal: false };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_upcoming"],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["To do"]);
    // The same Ticket off the Day reads identically in Remaining.
    expect(
      remainingWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: [],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["To do"]);
  });

  it("labels a closeout-ready Ticket as waiting for closeout, not To do", () => {
    const value = workspace();
    const readyForCloseout = {
      ...value.tickets[0],
      id: "t_ready",
      ticket_status: "empty",
      has_pending_proposal: false,
      waiting_to_closeout: true
    };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_ready"],
        tickets: [readyForCloseout]
      }).map((group) => group.label)
    ).toEqual(["Waiting for closeout"]);
    expect(
      remainingWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: [],
        tickets: [readyForCloseout]
      }).map((group) => group.label)
    ).toEqual(["Waiting for closeout"]);
  });

  it("routes Sprint Item artifacts through the shared managed preview", () => {
    const target = sprintItemFileTarget("si_workspace", "notes/proof.md");
    expect(target).not.toBeNull();
    expect(resolvePreview(target!).href).toBe(
      "/files/sprint-items/si_workspace/notes/proof.md"
    );
    expect(previewHashHref(target!)).toBe(
      "#/preview?source=sprint-item&item=si_workspace&path=notes%2Fproof.md"
    );
  });
});
