import { describe, expect, it } from "vitest";

import {
  buildWorkspaceRail,
  hiddenWorkspaceCardCount,
  workspaceCardNeedsUser,
  workspaceItemIsOpen,
  workspaceVisibleCards
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
  it("groups every Item, puts attention Items first, orders by Item priority, and leaves No Item last", () => {
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
    expect(rail.noItemCards.map((item) => item.id)).toEqual(["loose"]);
  });

  it("shows only user-owned live rows by default and reveals one Item or the whole rail", () => {
    const cards = [
      card("needs", { ticket_status: "needs_user" }),
      card("review", { ticket_status: "awaiting_user_review" }),
      card("paired", { ticket_status: "paired" }),
      card("owned", { ticket_status: "user" }),
      card("blocked-owned", { ticket_status: "user", blocked: true }),
      card("working", { ticket_status: "agent" }),
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

    expect(item.attentionCards.map((value) => value.id)).toEqual([
      "needs",
      "owned",
      "paired",
      "review"
    ]);
    expect(workspaceVisibleCards(item.cards, "attention").map((value) => value.id)).toEqual([
      "needs",
      "owned",
      "paired",
      "review"
    ]);
    expect(hiddenWorkspaceCardCount(item, "attention")).toBe(3);
    expect(workspaceVisibleCards(item.cards, "attention", true)).toHaveLength(7);
    expect(workspaceVisibleCards(item.cards, "all")).toHaveLength(7);
    expect(workspaceVisibleCards(item.cards, "all").some((value) => value.id === "done")).toBe(false);
    expect(workspaceCardNeedsUser(cards[4])).toBe(false);
  });

  it("keeps an Item with only done Tickets as a folded zero-count box", () => {
    const rail = buildWorkspaceRail([
      card("done", {
        stage: "done",
        is_done: true,
        sprint_item_id: "si_done",
        sprint_item_title: "Finished outcome",
        sprint_item_priority: "P1"
      })
    ]);

    expect(rail.items).toHaveLength(1);
    expect(rail.items[0].liveCards).toEqual([]);
    expect(workspaceVisibleCards(rail.items[0].cards, "attention")).toEqual([]);
    expect(workspaceItemIsOpen(rail.items[0], "attention")).toBe(false);
    expect(workspaceItemIsOpen(rail.items[0], "all")).toBe(false);
    expect(workspaceItemIsOpen(rail.items[0], "all", { opened: true })).toBe(false);
    expect(workspaceItemIsOpen(rail.items[0], "all", { expanded: true })).toBe(false);
  });

  it("derives Item disclosure from live work, mode, attention, and explicit folds", () => {
    const quiet = buildWorkspaceRail([
      card("quiet", {
        sprint_item_id: "si_quiet",
        sprint_item_title: "Quiet",
        sprint_item_priority: "P2"
      })
    ]).items[0];
    const attention = buildWorkspaceRail([
      card("attention", {
        sprint_item_id: "si_attention",
        sprint_item_title: "Attention",
        sprint_item_priority: "P2",
        ticket_status: "needs_user"
      })
    ]).items[0];

    expect(workspaceItemIsOpen(quiet, "attention")).toBe(false);
    expect(workspaceItemIsOpen(quiet, "attention", { opened: true })).toBe(true);
    expect(workspaceItemIsOpen(quiet, "attention", { expanded: true })).toBe(true);
    expect(workspaceItemIsOpen(quiet, "all")).toBe(true);
    expect(workspaceItemIsOpen(attention, "attention")).toBe(true);
    expect(workspaceItemIsOpen(attention, "all", { folded: true })).toBe(false);
  });
});
