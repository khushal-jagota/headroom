import { describe, expect, it } from "vitest";

import {
  buildWorkspaceRail,
  workspaceCardGroupKey,
  workspaceGroups
} from "../src/lib/workspaceRail";
import {
  parseWorkspaceAddress,
  whatTheAddressOpens
} from "../src/lib/workspaceAddress";
import { conversationSignalPresentation } from "../src/lib/conversationSignalPresentation";
import type { BoardCard, BoardSprintItem } from "../src/lib/types";
import { boardCard as card } from "./boardCardFixture";

function item(id: string, values: Partial<BoardSprintItem> = {}): BoardSprintItem {
  return {
    id,
    created_at: 0,
    conversation_id: null,
    awaiting_reply: false,
    awaiting_approval: false,
    assigned: false,
    agent_state: "idle",
    ticket_rollup: { awaiting_reply: false, awaiting_approval: false, assigned: false, agent_state: "idle" },
    ...values
  };
}

describe("Workspace rail", () => {
  it("names each card's group from shared attention facts", () => {
    expect(workspaceCardGroupKey(card("a", { awaiting_reply: true }))).toBe(
      "awaiting_reply"
    );
    expect(workspaceCardGroupKey(card("b", { assigned: true }))).toBe("assigned");
    expect(workspaceCardGroupKey(card("c", { awaiting_approval: true }))).toBe(
      "awaiting_approval"
    );
    expect(workspaceCardGroupKey(card("d", { assigned: true }))).toBe("assigned");
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
        card("a", { awaiting_approval: true, gating_field: "kickoff" })
      )
    ).toBe("waiting_for_kickoff");
    // The gating field only splits a Ticket that is awaiting approval.
    expect(
      workspaceCardGroupKey(card("b", { ticket_status: "agent", gating_field: "kickoff" }))
    ).toBe("agent");
  });

  it("orders and labels every group, and collapses the three quiet ones", () => {
    const groups = workspaceGroups([
      card("done", { stage: "done", is_done: true }),
      card("resting"),
      card("blocked", { ticket_status: "blocked" }),
      card("closeout", { waiting_to_closeout: true }),
      card("kickoff", { awaiting_approval: true, gating_field: "kickoff" }),
      card("review", { awaiting_approval: true }),
      card("working", { ticket_status: "agent" }),
      card("assigned", { assigned: true }),
      card("needs", { awaiting_reply: true }),
      card("failed", { ticket_status: "errored" })
    ]);

    expect(groups.map((group) => group.label)).toEqual([
      "Errored",
      "Needs you",
      "Assigned",
      "Agent",
      "Waiting to Closeout",
      "Awaiting approval",
      "Waiting for Kickoff",
      "Empty",
      "Blocked",
      "Done"
    ]);
    // A quiet group arrives shut, with its own count for the reader to open. It is
    // never absent: no status is filtered out of the rail.
    expect(
      groups.filter((group) => group.defaultCollapsed).map((group) => group.key)
    ).toEqual(["waiting_for_kickoff", "blocked", "done"]);
  });

  it("draws a board of only quiet Tickets rather than nothing", () => {
    const groups = workspaceGroups([
      card("done", { stage: "done", is_done: true }),
      card("blocked", { ticket_status: "blocked" }),
      card("closeout", { waiting_to_closeout: true }),
      card("resting")
    ]);

    expect(groups.map((group) => group.key)).toEqual([
      "waiting_to_closeout",
      "empty",
      "blocked",
      "done"
    ]);
    expect(groups.map((group) => group.cards.length)).toEqual([1, 1, 1, 1]);
  });

  it("sorts rows by activity, newest first", () => {
    const groups = workspaceGroups([
      card("older", { assigned: true, activity_at: 100 }),
      card("newest", { assigned: true, activity_at: 300 }),
      card("middle", { assigned: true, activity_at: 200 })
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
        card("owned", { assigned: true, sprint_item_id: "si_one" }),
        card("shut", { ticket_status: "blocked", sprint_item_id: "si_one" }),
        card("loose", { assigned: true })
      ],
      [item("si_one")]
    );

    // The Tickets view holds every card, including one with no Sprint Item.
    expect(rail.groups.flatMap((group) => group.cards.map((entry) => entry.id))).toEqual([
      "loose",
      "owned",
      "shut"
    ]);
    // An Item's own groups are the same groups, shut the same way.
    expect(rail.items[0].groups.map((group) => group.label)).toEqual([
      "Assigned",
      "Blocked"
    ]);
    expect(rail.items[0].groups.map((group) => group.defaultCollapsed)).toEqual([
      false,
      true
    ]);
  });

  it("orders Items by priority then a fixed creation-time tie-break", () => {
    const rail = buildWorkspaceRail(
      [
        card("quiet-p0", { sprint_item_id: "si_quiet", sprint_item_priority: "P0" }),
        card("older-p1", {
          sprint_item_id: "si_older",
          sprint_item_priority: "P1",
          awaiting_reply: true
        }),
        card("newer-p1", {
          sprint_item_id: "si_newer",
          sprint_item_priority: "P1",
          assigned: true
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
    const oneNeedsReply = buildWorkspaceRail(
      [
        card("older", {
          sprint_item_id: "si_older",
          sprint_item_priority: "P1",
          awaiting_reply: true,
          activity_at: 999
        }),
        card("newer", { sprint_item_id: "si_newer", sprint_item_priority: "P1" })
      ],
      summaries
    );

    expect(quiet.items.map((entry) => entry.id)).toEqual(["si_older", "si_newer"]);
    expect(oneNeedsReply.items.map((entry) => entry.id)).toEqual(["si_older", "si_newer"]);
  });

  it("marks an Item from its own supervisor, not from its Tickets", () => {
    const rail = buildWorkspaceRail(
      [
        card("its-ticket", {
          sprint_item_id: "si_one",
          conversation_id: "conv-worker",
          agent_state: "working"
        })
      ],
      [item("si_one", { conversation_id: "conv-supervisor", awaiting_reply: true })]
    );

    expect(rail.items[0].signals).toEqual({
      awaiting_reply: true,
      agent_state: "idle"
    });
  });

  it("takes an Item's attention from its child Ticket rollup", () => {
    const rail = buildWorkspaceRail(
      [card("its-ticket", { sprint_item_id: "si_one" })],
      [
        item("si_one", {
          ticket_rollup: { awaiting_reply: true, awaiting_approval: false, assigned: false, agent_state: "idle" }
        })
      ]
    );

    expect(conversationSignalPresentation(rail.items[0].signals).state).toBe("needs-me");
  });

  it("marks an Item its conversation has replied on", () => {
    const rail = buildWorkspaceRail(
      [card("its-ticket", { sprint_item_id: "si_one" })],
      [item("si_one", { conversation_id: "conv-supervisor", awaiting_reply: true })]
    );

    expect(
      conversationSignalPresentation(rail.items[0].signals).state
    ).toBe("needs-me");
  });

  it("leaves an Item its conversation has never spoken on unlit", () => {
    const rail = buildWorkspaceRail(
      [card("its-ticket", { sprint_item_id: "si_one" })],
      [item("si_one", { conversation_id: "conv-supervisor" })]
    );

    expect(
      conversationSignalPresentation(rail.items[0].signals).state
    ).toBe("upcoming");
  });

  it("says nothing about an Item the board has no summary for", () => {
    const rail = buildWorkspaceRail([card("orphan", { sprint_item_id: "si_gone" })], []);

    expect(rail.items[0].signals).toEqual({ awaiting_reply: false, agent_state: "idle" });
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

    // The Item keeps its box, and its Done group with it. A rested Item counts and
    // names its finished work, shut.
    expect(rail.items[0].title).toBe("Finished outcome");
    expect(rail.items[0].groups.map((group) => group.label)).toEqual(["Done"]);
    expect(rail.items[0].groups[0].defaultCollapsed).toBe(true);
    expect(rail.items[0].rested).toBe(true);
  });

  it("marks an Item rested only when every one of its Tickets is done", () => {
    const oneTicket = buildWorkspaceRail(
      [card("done", { is_done: true, sprint_item_id: "si_one" })],
      [item("si_one")]
    );
    expect(oneTicket.items[0].rested).toBe(true);

    const mixed = buildWorkspaceRail(
      [
        card("done", { is_done: true, sprint_item_id: "si_two" }),
        card("live", { ticket_status: "agent", sprint_item_id: "si_two" })
      ],
      [item("si_two")]
    );
    expect(mixed.items[0].rested).toBe(false);

    // A Blocked Ticket is not done, so it keeps the Item awake even though it draws
    // no group of its own in the rail.
    const blocked = buildWorkspaceRail(
      [card("blocked", { ticket_status: "blocked", sprint_item_id: "si_three" })],
      [item("si_three")]
    );
    expect(blocked.items[0].rested).toBe(false);
  });
});

describe("What the rail draws open", () => {
  function opensAt(hash: string) {
    const address = parseWorkspaceAddress(hash);
    if (!address) throw new Error(`not a Workspace address: ${hash}`);
    return whatTheAddressOpens(address);
  }

  it("opens no Item for the Chief of Staff", () => {
    expect(opensAt("#/workspace/chief-of-staff")).toEqual({
      view: "items",
      openItemId: null,
      markedItemId: null,
      markedTicketId: null,
      chiefMarked: true
    });
    // The Chief is still the Chief while the rail shows the Tickets list.
    expect(opensAt("#/workspace/chief-of-staff?view=tickets").openItemId).toBeNull();
  });

  it("opens no Item for a Ticket picked out of the Tickets list", () => {
    expect(opensAt("#/workspace/t_one")).toEqual({
      view: "tickets",
      openItemId: null,
      markedItemId: null,
      markedTicketId: "t_one",
      chiefMarked: false
    });
  });

  it("opens the Item a Ticket was opened from, and marks the Ticket", () => {
    expect(opensAt("#/workspace/item/si_one/t_one")).toEqual({
      view: "items",
      openItemId: "si_one",
      markedItemId: null,
      markedTicketId: "t_one",
      chiefMarked: false
    });
  });

  it("marks the Item itself when the Item is what the address names", () => {
    expect(opensAt("#/workspace/item/si_one")).toEqual({
      view: "items",
      openItemId: "si_one",
      markedItemId: "si_one",
      markedTicketId: null,
      chiefMarked: false
    });
  });

  it("opens nothing at the bare Workspace address", () => {
    expect(opensAt("#/workspace")).toEqual({
      view: "tickets",
      openItemId: null,
      markedItemId: null,
      markedTicketId: null,
      chiefMarked: false
    });
  });

  it("draws the same thing every time for the same address", () => {
    const addresses = [
      "#/workspace",
      "#/workspace?view=items",
      "#/workspace/chief-of-staff",
      "#/workspace/t_one",
      "#/workspace/item/si_one",
      "#/workspace/item/si_one/t_one",
      "#/workspace/item/si_one?view=tickets"
    ];
    for (const hash of addresses) {
      expect(opensAt(hash)).toEqual(opensAt(hash));
    }
    // Every address opens at most one Item and marks at most one row, so a reload, a
    // Back, or a change signal landing underneath cannot move what is open.
    for (const hash of addresses) {
      const opening = opensAt(hash);
      const marked = [
        opening.markedItemId,
        opening.markedTicketId,
        opening.chiefMarked ? "chief" : null
      ].filter(Boolean);
      expect(marked.length).toBeLessThanOrEqual(1);
    }
  });
});
