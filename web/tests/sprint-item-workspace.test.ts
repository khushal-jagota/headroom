import { describe, expect, it } from "vitest";
import {
  remainingWorkspaceTicketGroups,
  todayWorkspaceTicketGroups,
  workspaceArtifactRows,
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
        ticket_status: "awaiting_approval",
        waiting_to_closeout: false,
        has_pending_proposal: true,
        gating_field: "implementation",
        blocked: false,
        review_route: "propose",
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
        gating_field: null,
        blocked: false,
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
        gating_field: null,
        blocked: false,
        review_route: "stop",
        worker_type: "coding",
        day_ids: []
      }
    ],
    artifacts: ["proof.md"],
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

  it("labels a not-yet-started Ticket with the word the rail uses", () => {
    const value = workspace();
    const upcoming = { ...value.tickets[0], id: "t_upcoming", ticket_status: "empty", has_pending_proposal: false };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_upcoming"],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["Not started"]);
    // The same Ticket off the Day reads identically in Remaining.
    expect(
      remainingWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: [],
        tickets: [upcoming]
      }).map((group) => group.label)
    ).toEqual(["Not started"]);
  });

  it("labels a closeout-ready Ticket as waiting for closeout, not Not started", () => {
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

  it("reads Blocked from an open blocker link, not only from the status", () => {
    const value = workspace();
    const held = { ...value.tickets[0], id: "t_held", blocked: true };
    expect(
      todayWorkspaceTicketGroups({
        ...value,
        today_ticket_ids: ["t_held"],
        tickets: [held]
      }).map((group) => group.label)
    ).toEqual(["Blocked"]);
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

  it("gives a real artifact row a working href and a genuinely unresolvable one null, never empty", () => {
    const value = { ...workspace(), artifacts: ["artifacts/proof.md", "../escape.md"] };
    const rows = workspaceArtifactRows(value);
    expect(rows).toEqual([
      {
        path: "artifacts/proof.md",
        label: "proof.md",
        kind: "md",
        href: "#/preview?source=sprint-item&item=si_workspace&path=artifacts%2Fproof.md"
      },
      { path: "../escape.md", label: "escape.md", kind: "md", href: null }
    ]);
    expect(rows.every((row) => row.href !== "")).toBe(true);
  });
});
