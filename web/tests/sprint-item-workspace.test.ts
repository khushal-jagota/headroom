import { describe, expect, it } from "vitest";
import {
  failedWorkspaceDeliveries,
  remainingWorkspaceTickets,
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
  it("groups only active Today work and keeps done or off-day Tickets separate", () => {
    const value = workspace();
    expect(todayWorkspaceTicketGroups(value).map((group) => [group.label, group.tickets.map((ticket) => ticket.id)])).toEqual([
      ["Awaiting approval", ["t_review"]]
    ]);
    expect(remainingWorkspaceTickets(value).map((ticket) => ticket.id)).toEqual([
      "t_later",
      "t_done"
    ]);
    expect(workspaceProgress(value)).toBe("1 of 3 done");
    expect(failedWorkspaceDeliveries(value).map((obligation) => obligation.id)).toEqual([
      "so_failed"
    ]);
  });

  it("labels a not-yet-started Ticket with the word used everywhere else", () => {
    const value = workspace();
    const upcoming = { ...value.tickets[0], id: "t_upcoming", ticket_status: "empty", has_pending_proposal: false };
    const groups = todayWorkspaceTicketGroups({
      ...value,
      today_ticket_ids: ["t_upcoming"],
      tickets: [upcoming]
    });
    expect(groups.map((group) => group.label)).toEqual(["To do"]);
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
