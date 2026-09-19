import { describe, expect, it } from "vitest";
import {
  remainingWorkspaceTicketGroups,
  todayWorkspaceTicketGroups,
  workspaceProgress,
  workspaceTicketIsBacklog
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
    created_at: 1,
    updated_at: 1,
    kind: "normal",
    awaiting_reply: false,
    awaiting_approval: false,
    assigned: false,
    agent_state: "idle",
    committed_sprints: [{ id: "sp_current", name: "Current", date_start: "2026-08-10", date_end: "2026-08-16" }],
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
        ticket_status: "awaiting_approval",
        awaiting_reply: false,
        awaiting_approval: true,
        assigned: false,
        agent_state: "idle",
        waiting_to_closeout: false,
        has_pending_proposal: true,
        gating_field: "implementation",
        blocked: false,
        review_route: "propose",
        worker_type: "coding",
        day_ids: ["day_2026-08-12"], sprint_id: "sp_old", sprint_name: "Previous"
      },
      {
        id: "t_done",
        title: "Already done",
        stage: "done",
        priority: "P3",
        ticket_status: "empty",
        awaiting_reply: false,
        awaiting_approval: false,
        assigned: false,
        agent_state: "idle",
        waiting_to_closeout: false,
        has_pending_proposal: false,
        gating_field: null,
        blocked: false,
        review_route: "stop",
        worker_type: "coding",
        day_ids: ["day_2026-08-12"], sprint_id: "sp_current", sprint_name: "Current"
      },
      {
        id: "t_later",
        title: "Later",
        stage: "needs_success",
        priority: "P2",
        ticket_status: "agent",
        awaiting_reply: false,
        awaiting_approval: false,
        assigned: false,
        agent_state: "working",
        waiting_to_closeout: false,
        has_pending_proposal: false,
        gating_field: null,
        blocked: false,
        review_route: "stop",
        worker_type: "coding",
        day_ids: [], sprint_id: null, sprint_name: null
      }
    ],
    artifacts: [{ path: "proof.md", modified_at: 1 }],
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
    expect(workspaceProgress(value)).toBe("2 open");
    expect(
      workspaceProgress({
        ...value,
        tickets: [{ ...value.tickets[0], awaiting_reply: true, awaiting_approval: false, has_pending_proposal: false }]
      })
    ).toBe("1 open · 1 needs you");
  });

  it("marks only open Tickets without a Sprint as backlog", () => {
    const value = workspace();
    expect(value.tickets.map(workspaceTicketIsBacklog)).toEqual([false, false, true]);
    expect(workspaceTicketIsBacklog({ ...value.tickets[2], stage: "done" })).toBe(false);
  });

  it("calls zero children No Tickets", () => {
    const value = workspace();
    expect(workspaceProgress({ ...value, tickets: [] })).toBe("No Tickets");
    expect(workspaceProgress({ ...value, tickets: [{ ...value.tickets[0], stage: "done" }] })).toBe("All 1 done");
  });

  it("gathers every parked proposal into the one Awaiting approval group", () => {
    const value = workspace();
    const parked = { ...value.tickets[0], id: "t_parked_one" };
    const alsoParked = { ...value.tickets[0], id: "t_parked_two" };
    const groups = todayWorkspaceTicketGroups({
      ...value,
      today_ticket_ids: ["t_parked_one", "t_parked_two"],
      tickets: [parked, alsoParked]
    });
    expect(
      groups.map((group) => [group.label, group.tickets.map((ticket) => ticket.id)])
    ).toEqual([["Awaiting approval", ["t_parked_one", "t_parked_two"]]]);
  });

  it("labels a resting Ticket with the word the rail uses", () => {
    const value = workspace();
    const upcoming = { ...value.tickets[0], id: "t_upcoming", ticket_status: "empty", awaiting_approval: false, has_pending_proposal: false };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_upcoming"],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["Empty"]);
    // The same Ticket off the Day reads identically in Remaining.
    expect(
      remainingWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: [],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["Empty"]);
  });

  it("labels a closeout-ready Ticket as waiting for closeout, not Empty", () => {
    const value = workspace();
    const readyForCloseout = {
      ...value.tickets[0],
      id: "t_ready",
      ticket_status: "empty",
      awaiting_approval: false,
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

  it("names a kickoff-gated Ticket on its own, as the rail does", () => {
    const value = workspace();
    const kickoff = {
      ...value.tickets[0],
      id: "t_kickoff",
      stage: "waiting_for_kickoff",
      gating_field: "kickoff"
    };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_kickoff", "t_review"],
        tickets: [kickoff, value.tickets[0]]
      }).map((group) => group.label)
    ).toEqual(["Waiting for kickoff", "Awaiting approval"]);
  });

  it("reads Errored and Blocked apart, each from the Ticket's own status", () => {
    const value = workspace();
    const errored = { ...value.tickets[2], id: "t_errored", ticket_status: "errored" };
    const blocked = { ...value.tickets[2], id: "t_blocked", ticket_status: "blocked" };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_errored", "t_blocked"],
        tickets: [errored, blocked]
      }).map((group) => [group.label, group.tickets.map((ticket) => ticket.id)])
    ).toEqual([
      ["Errored", ["t_errored"]],
      ["Blocked", ["t_blocked"]]
    ]);
  });

  it("lets an active Ticket keep its own group while a blocker link is open", () => {
    // The server writes status `blocked` only for a resting Ticket. A Ticket that is
    // doing something owns its status, and the rail reads it the same way.
    const value = workspace();
    const held = { ...value.tickets[2], id: "t_held", blocked: true };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_held"],
        tickets: [held]
      }).map((group) => group.label)
    ).toEqual(["Agent"]);
  });

  it("carries the quiet flag each group is opened or collapsed by", () => {
    const value = workspace();
    expect(
      todayWorkspaceTicketGroups(value).map((group) => [group.label, group.quiet])
    ).toEqual([
      ["Awaiting approval", false],
      ["Done", true]
    ]);
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
