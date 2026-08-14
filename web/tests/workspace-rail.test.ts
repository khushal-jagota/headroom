import { describe, expect, it } from "vitest";

import {
  buildWorkspaceRail,
  hiddenWorkspaceCardCount,
  workspaceCardGroupKey,
  workspaceGroupsHaveShownCards,
  workspaceItemGroups
} from "../src/lib/workspaceRail";
import type { BoardCard } from "../src/lib/types";

function card(id: string, values: Partial<BoardCard> = {}): BoardCard {
  return {
    id,
    title: id,
    priority: "P3",
    deadline: null,
    project_id: null,
    project: null,
    group_project_id: null,
    group_project: null,
    activity_at: 0,
    has_pending_proposal: false,
    ticket_status: "empty",
    backend_error: null,
    worker_type: "coding",
    employee_backend: "codex",
    stage: "needs_implementation",
    stage_label: "Implementation",
    gating_field: "implementation",
    gating_field_label: "Implementation",
    is_done: false,
    is_dropped: false,
    blocked: false,
    conversation_id: null,
    waiting_to_closeout: false,
    sprint_item_id: null,
    sprint_item_title: null,
    sprint_item_priority: null,
    agent_working: false,
    needs_me: false,
    latest_turn_ended_sequence: 0,
    ...values
  };
}

describe("Workspace rail", () => {
  it("groups every Item, puts Items the user owns work in first, then Item priority", () => {
    const rail = buildWorkspaceRail([
      card("quiet-p0", {
        sprint_item_id: "si_quiet",
        sprint_item_title: "Quiet",
        sprint_item_priority: "P0"
      }),
      card("attention-p2", {
        sprint_item_id: "si_attention",
        sprint_item_title: "Attention",
        sprint_item_priority: "P2",
        ticket_status: "needs_user"
      }),
      card("attention-p1", {
        sprint_item_id: "si_first",
        sprint_item_title: "First",
        sprint_item_priority: "P1",
        ticket_status: "paired"
      }),
      card("loose", { ticket_status: "user" })
    ]);

    expect(rail.items.map((item) => item.id)).toEqual([
      "si_first",
      "si_attention",
      "si_quiet"
    ]);
    expect(rail.noItemGroups.flatMap((group) => group.cards.map((value) => value.id))).toEqual([
      "loose"
    ]);
  });

  it("names each card's group from the shared Ticket condition", () => {
    expect(workspaceCardGroupKey(card("a", { ticket_status: "needs_user" }))).toBe("needs-me");
    expect(workspaceCardGroupKey(card("b", { ticket_status: "user" }))).toBe("needs-me");
    expect(workspaceCardGroupKey(card("c", { ticket_status: "awaiting_user_review" }))).toBe(
      "awaiting-user-review"
    );
    expect(workspaceCardGroupKey(card("c2", { ticket_status: "awaiting_agent_review" }))).toBe(
      "awaiting-agent-review"
    );
    expect(workspaceCardGroupKey(card("d", { has_pending_proposal: true }))).toBe(
      "awaiting-user-review"
    );
    expect(workspaceCardGroupKey(card("e", { ticket_status: "paired" }))).toBe("current-paired");
    expect(workspaceCardGroupKey(card("f", { ticket_status: "agent" }))).toBe("current-running");
    expect(workspaceCardGroupKey(card("g", { ticket_status: "blocked" }))).toBe("errored");
    // A link blocker is not a status, and it still lands the card in Blocked.
    expect(workspaceCardGroupKey(card("h", { ticket_status: "user", blocked: true }))).toBe(
      "errored"
    );
    expect(workspaceCardGroupKey(card("i"))).toBe("upcoming");
    expect(workspaceCardGroupKey(card("j", { stage: "done", is_done: true }))).toBe("completed");
    expect(workspaceCardGroupKey(card("k", { waiting_to_closeout: true }))).toBe(
      "current-waiting"
    );
  });

  it("shows a closeout-ready card as waiting for closeout, not lumped in with To do", () => {
    const item = buildWorkspaceRail([
      card("ready", { waiting_to_closeout: true, sprint_item_id: "si_one" }),
      card("idle", { sprint_item_id: "si_one" })
    ]).items[0];

    expect(item.groups.map((group) => group.label)).toEqual(["Waiting for closeout", "To do"]);
    // Not one of the quiet-three hidden groups: it shows without revealing.
    expect(workspaceItemGroups(item.groups, false).map((group) => group.label)).toEqual([
      "Waiting for closeout",
      "To do"
    ]);
  });

  it("orders the groups, hides the quiet four, and reveals them on request", () => {
    const cards = [
      card("needs", { ticket_status: "needs_user" }),
      card("review", { ticket_status: "awaiting_user_review" }),
      card("agent-review", { ticket_status: "awaiting_agent_review" }),
      card("paired", { ticket_status: "paired" }),
      card("working", { ticket_status: "agent" }),
      card("blocked", { ticket_status: "user", blocked: true }),
      card("resting"),
      card("done", { stage: "done", is_done: true })
    ];
    const item = buildWorkspaceRail(
      cards.map((value) => ({
        ...value,
        sprint_item_id: "si_one",
        sprint_item_title: "One",
        sprint_item_priority: "P2"
      }))
    ).items[0];

    expect(item.groups.map((group) => group.label)).toEqual([
      "Needs user",
      "User review",
      "Agent review",
      "Paired",
      "Agent",
      "Blocked",
      "To do",
      "Done"
    ]);
    // Agent review is hidden by default, same as Agent, Blocked, and Done.
    expect(workspaceItemGroups(item.groups, false).map((group) => group.label)).toEqual([
      "Needs user",
      "User review",
      "Paired",
      "To do"
    ]);
    expect(workspaceItemGroups(item.groups, true)).toHaveLength(8);
    expect(hiddenWorkspaceCardCount(item.groups)).toBe(4);
    expect(item.needsUser).toBe(true);
  });

  it("carries the Item's project and its progress across every Ticket", () => {
    const rail = buildWorkspaceRail(
      [
        card("on-today", {
          sprint_item_id: "si_one",
          sprint_item_title: "One",
          sprint_item_priority: "P1"
        })
      ],
      [{ id: "si_one", project: "Panels", done_ticket_count: 3, total_ticket_count: 7 }]
    );

    expect(rail.items[0].project).toBe("Panels");
    expect(rail.items[0].progress).toEqual({ done: 3, total: 7 });
  });

  it("keeps an Item whose Tickets are all quiet, with nothing shown until it is revealed", () => {
    const item = buildWorkspaceRail([
      card("done", {
        stage: "done",
        is_done: true,
        sprint_item_id: "si_done",
        sprint_item_title: "Finished outcome",
        sprint_item_priority: "P1"
      })
    ]).items[0];

    expect(item.needsUser).toBe(false);
    expect(workspaceItemGroups(item.groups, false)).toEqual([]);
    expect(workspaceGroupsHaveShownCards(item.groups)).toBe(false);
    expect(hiddenWorkspaceCardCount(item.groups)).toBe(1);
    expect(workspaceItemGroups(item.groups, true).map((group) => group.label)).toEqual(["Done"]);
  });
});
