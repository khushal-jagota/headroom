import { describe, expect, it } from "vitest";

import {
  buildWorkspaceRail,
  workspaceCardGroupKey,
  workspaceGroups
} from "../src/lib/workspaceRail";
import type { BoardCard, BoardSprintItem } from "../src/lib/types";

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

function item(id: string, values: Partial<BoardSprintItem> = {}): BoardSprintItem {
  return {
    id,
    created_at: 0,
    conversation_id: null,
    agent_working: false,
    needs_me: false,
    latest_turn_ended_sequence: 0,
    ...values
  };
}

describe("Workspace rail", () => {
  it("names each card's group from its own status", () => {
    expect(workspaceCardGroupKey(card("a", { ticket_status: "needs_user" }))).toBe(
      "needs_user"
    );
    expect(workspaceCardGroupKey(card("b", { ticket_status: "user" }))).toBe("user");
    expect(workspaceCardGroupKey(card("c", { ticket_status: "awaiting_approval" }))).toBe(
      "awaiting_approval"
    );
    expect(workspaceCardGroupKey(card("d", { ticket_status: "paired" }))).toBe("paired");
    expect(workspaceCardGroupKey(card("e", { ticket_status: "agent" }))).toBe("agent");
    expect(workspaceCardGroupKey(card("f", { ticket_status: "errored" }))).toBe("errored");
    expect(workspaceCardGroupKey(card("g", { ticket_status: "blocked" }))).toBe("blocked");
    expect(workspaceCardGroupKey(card("h"))).toBe("empty");
    // Done wins over the status, and a runnable Closeout is its own answer.
    expect(workspaceCardGroupKey(card("i", { stage: "done", is_done: true }))).toBe("done");
    expect(workspaceCardGroupKey(card("j", { waiting_to_closeout: true }))).toBe(
      "waiting_to_closeout"
    );
  });

  it("splits a kickoff approval from every later approval", () => {
    expect(
      workspaceCardGroupKey(
        card("a", { ticket_status: "awaiting_approval", gating_field: "kickoff" })
      )
    ).toBe("waiting_for_kickoff");
    // The gating field only splits a Ticket that is awaiting approval.
    expect(
      workspaceCardGroupKey(card("b", { ticket_status: "agent", gating_field: "kickoff" }))
    ).toBe("agent");
  });

  it("orders and labels the groups the rail holds, and leaves out the quiet ones", () => {
    const groups = workspaceGroups([
      card("done", { stage: "done", is_done: true }),
      card("resting"),
      card("blocked", { ticket_status: "blocked" }),
      card("closeout", { waiting_to_closeout: true }),
      card("kickoff", { ticket_status: "awaiting_approval", gating_field: "kickoff" }),
      card("review", { ticket_status: "awaiting_approval" }),
      card("working", { ticket_status: "agent" }),
      card("paired", { ticket_status: "paired" }),
      card("mine", { ticket_status: "user" }),
      card("needs", { ticket_status: "needs_user" }),
      card("failed", { ticket_status: "errored" })
    ]);

    expect(groups.map((group) => group.label)).toEqual([
      "Errored",
      "Needs you",
      "User",
      "Paired",
      "Agent",
      "Awaiting approval",
      "Waiting for Kickoff"
    ]);
    // The quiet states are not drawn shut here. They are not in the rail at all, and
    // the Sprint Item page is where they are read.
    expect(groups.map((group) => group.key)).not.toContain("done");
    expect(groups.map((group) => group.key)).not.toContain("blocked");
    expect(groups.map((group) => group.key)).not.toContain("waiting_to_closeout");
    expect(groups.map((group) => group.key)).not.toContain("empty");
  });

  it("sorts rows by activity, newest first", () => {
    const groups = workspaceGroups([
      card("older", { ticket_status: "paired", activity_at: 100 }),
      card("newest", { ticket_status: "paired", activity_at: 300 }),
      card("middle", { ticket_status: "paired", activity_at: 200 })
    ]);

    expect(groups[0].cards.map((entry) => entry.id)).toEqual([
      "newest",
      "middle",
      "older"
    ]);
  });

  it("gives both views the same groups over one board", () => {
    const rail = buildWorkspaceRail(
      [
        card("owned", { ticket_status: "paired", sprint_item_id: "si_one" }),
        card("loose", { ticket_status: "user" })
      ],
      [item("si_one")]
    );

    // The Tickets view holds every card, including one with no Sprint Item.
    expect(rail.groups.flatMap((group) => group.cards.map((entry) => entry.id))).toEqual([
      "loose",
      "owned"
    ]);
    expect(rail.items[0].groups.map((group) => group.label)).toEqual(["Paired"]);
  });

  it("orders Items by priority then a fixed creation-time tie-break", () => {
    const rail = buildWorkspaceRail(
      [
        card("quiet-p0", { sprint_item_id: "si_quiet", sprint_item_priority: "P0" }),
        card("older-p1", {
          sprint_item_id: "si_older",
          sprint_item_priority: "P1",
          ticket_status: "needs_user"
        }),
        card("newer-p1", {
          sprint_item_id: "si_newer",
          sprint_item_priority: "P1",
          ticket_status: "paired"
        })
      ],
      [
        item("si_quiet", { created_at: 50 }),
        item("si_older", { created_at: 100 }),
        item("si_newer", { created_at: 200 })
      ]
    );

    expect(rail.items.map((entry) => entry.id)).toEqual([
      "si_quiet",
      "si_older",
      "si_newer"
    ]);
  });

  it("never moves an Item when a card's status or activity changes", () => {
    const summaries = [item("si_older", { created_at: 100 }), item("si_newer", { created_at: 200 })];
    const quiet = buildWorkspaceRail(
      [
        card("older", { sprint_item_id: "si_older", sprint_item_priority: "P1" }),
        card("newer", { sprint_item_id: "si_newer", sprint_item_priority: "P1" })
      ],
      summaries
    );
    const oneNeedsUser = buildWorkspaceRail(
      [
        card("older", {
          sprint_item_id: "si_older",
          sprint_item_priority: "P1",
          ticket_status: "needs_user",
          activity_at: 999
        }),
        card("newer", { sprint_item_id: "si_newer", sprint_item_priority: "P1" })
      ],
      summaries
    );

    expect(quiet.items.map((entry) => entry.id)).toEqual(["si_older", "si_newer"]);
    expect(oneNeedsUser.items.map((entry) => entry.id)).toEqual(["si_older", "si_newer"]);
  });

  it("marks an Item from its own supervisor, not from its Tickets", () => {
    const rail = buildWorkspaceRail(
      [
        card("its-ticket", {
          sprint_item_id: "si_one",
          conversation_id: "conv-worker",
          agent_working: true
        })
      ],
      [item("si_one", { conversation_id: "conv-supervisor", needs_me: true })]
    );

    expect(rail.items[0].signals).toEqual({
      conversation_id: "conv-supervisor",
      needs_me: true,
      agent_working: false,
      latest_turn_ended_sequence: 0
    });
  });

  it("says nothing about an Item the board has no summary for", () => {
    const rail = buildWorkspaceRail([card("orphan", { sprint_item_id: "si_gone" })], []);

    expect(rail.items[0].signals.conversation_id).toBeNull();
    expect(rail.items[0].createdAt).toBe(0);
  });

  it("keeps an Item whose Tickets are all finished", () => {
    const rail = buildWorkspaceRail(
      [
        card("done", {
          stage: "done",
          is_done: true,
          sprint_item_id: "si_done",
          sprint_item_title: "Finished outcome"
        })
      ],
      [item("si_done")]
    );

    // The Item is still the door to its page, so it keeps its box. Done is not a rail
    // group, so the box has nothing to count and says nothing.
    expect(rail.items[0].title).toBe("Finished outcome");
    expect(rail.items[0].groups).toEqual([]);
  });
});
