import { describe, expect, it } from "vitest";

import {
  buildWorkspaceRail,
  workspaceCardGroupKey,
  workspaceGroups,
  workspaceRowMarkPresentation,
  workspaceSprintItemRowMark,
  workspaceTicketRowMark
} from "../src/lib/workspaceRail";
import {
  parseWorkspaceAddress,
  whatTheAddressOpens
} from "../src/lib/workspaceAddress";
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

  // Nothing in production writes `ticket_status = 'errored'`, so the raw status named no
  // broken worker at all. `agent_state` carries the status or a last turn that ended
  // failed, and it leads, exactly as it does on the Sprint Item page.
  it("names a broken worker Errored from its failed last turn, ahead of attention", () => {
    expect(workspaceCardGroupKey(card("failed", { agent_state: "errored" }))).toBe("errored");
    expect(
      workspaceCardGroupKey(
        card("failed-dispatched", { ticket_status: "agent", agent_state: "errored" })
      )
    ).toBe("errored");
    expect(
      workspaceCardGroupKey(
        card("failed-approval", { awaiting_approval: true, agent_state: "errored" })
      )
    ).toBe("errored");
    // A finished Ticket stays finished.
    expect(
      workspaceCardGroupKey(
        card("failed-done", { stage: "done", is_done: true, agent_state: "errored" })
      )
    ).toBe("done");
  });

  it("keeps kickoff approvals in the owner attention group", () => {
    expect(
      workspaceCardGroupKey(
        card("a", { awaiting_approval: true, gating_field: "brief" })
      )
    ).toBe("awaiting_approval");
    // The gating field does not classify a Ticket without current attention.
    expect(
      workspaceCardGroupKey(card("b", { ticket_status: "agent", gating_field: "brief" }))
    ).toBe("agent");
  });

  it("puts the three owner attention groups before every remaining group", () => {
    const groups = workspaceGroups([
      card("done", { stage: "done", is_done: true }),
      card("resting"),
      card("blocked", { ticket_status: "blocked" }),
      card("closeout", { waiting_to_closeout: true }),
      card("non-owner-approval", { ticket_status: "awaiting_approval" }),
      card("kickoff", { awaiting_approval: true, gating_field: "brief" }),
      card("review", { awaiting_approval: true }),
      card("working", { ticket_status: "agent" }),
      card("assigned", { assigned: true }),
      card("needs", { awaiting_reply: true }),
      card("failed", { ticket_status: "errored" })
    ]);

    expect(groups.map((group) => group.label)).toEqual([
      "Awaiting approval",
      "Assigned",
      "Messages",
      "Errored",
      "Agent",
      "Waiting on Consequences",
      "Awaiting an agent's approval",
      "Empty",
      "Blocked",
      "Done"
    ]);
    // Quiet status groups arrive shut, with their own count for the reader to open.
    expect(
      groups.filter((group) => group.defaultCollapsed).map((group) => group.key)
    ).toEqual(["blocked", "done"]);
  });

  it("keeps a non-owner awaiting-approval status in the old remainder position", () => {
    const nonOwner = card("non-owner", {
      ticket_status: "awaiting_approval",
      awaiting_approval: false
    });
    expect(workspaceCardGroupKey(nonOwner)).toBe("status_awaiting_approval");

    const groups = workspaceGroups([
      nonOwner,
      card("owner", { awaiting_approval: true }),
      card("closeout", { waiting_to_closeout: true }),
      card("empty")
    ]);
    expect(groups.map((group) => group.key)).toEqual([
      "awaiting_approval",
      "waiting_to_closeout",
      "status_awaiting_approval",
      "empty"
    ]);
    expect(groups.map((group) => group.cards.map((entry) => entry.id))).toEqual([
      ["owner"],
      ["closeout"],
      ["non-owner"],
      ["empty"]
    ]);
  });

  it("gives every visible Tickets group a distinct label", () => {
    const groups = workspaceGroups([
      card("owner", { awaiting_approval: true }),
      card("agent-owner", {
        ticket_status: "awaiting_approval",
        awaiting_approval: false
      })
    ]);
    const labels = groups.map((group) => group.label);

    expect(labels).toEqual([
      "Awaiting approval",
      "Awaiting an agent's approval"
    ]);
    expect(new Set(labels).size).toBe(labels.length);
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

  it("shows only non-empty attention groups beneath a Sprint Item", () => {
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
    // Quiet child Tickets remain in the Tickets view, not under the Item heading.
    expect(rail.items[0].groups.map((group) => group.label)).toEqual([
      "Assigned"
    ]);
  });

  it("places each overlapping child Ticket in only its first attention group", () => {
    const rail = buildWorkspaceRail(
      [
        card("all-three", {
          sprint_item_id: "si_one",
          awaiting_approval: true,
          assigned: true,
          awaiting_reply: true
        }),
        card("reply", { sprint_item_id: "si_one", awaiting_reply: true })
      ],
      [item("si_one")]
    );

    expect(rail.items[0].groups.map((group) => group.label)).toEqual([
      "Awaiting approval",
      "Messages"
    ]);
    expect(
      rail.items[0].groups.flatMap((group) => group.cards.map((entry) => entry.id))
    ).toEqual(["all-three", "reply"]);
  });

  it("keeps a broken worker's own attention group beneath a Sprint Item", () => {
    // An Item is read at rest, so it holds to the three groups. The Tickets view is the
    // screen that leads with a broken worker, and it still does.
    const rail = buildWorkspaceRail(
      [
        card("broken-approval", {
          sprint_item_id: "si_one",
          awaiting_approval: true,
          agent_state: "errored"
        }),
        card("broken-quiet", { sprint_item_id: "si_one", agent_state: "errored" })
      ],
      [item("si_one")]
    );

    expect(rail.items[0].groups.map((group) => group.label)).toEqual([
      "Awaiting approval"
    ]);
    expect(
      rail.items[0].groups.flatMap((group) => group.cards.map((entry) => entry.id))
    ).toEqual(["broken-approval"]);
    // The Tickets view still names both broken workers first, and names them Errored.
    expect(rail.groups.map((group) => group.key)).toEqual(["errored"]);
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

  it("marks an Item from its own attention and the complete Ticket rollup", () => {
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

    expect(rail.items[0].mark).toBe("attention");
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

    expect(rail.items[0].mark).toBe("attention");
  });

  it("marks an Item its conversation has replied on", () => {
    const rail = buildWorkspaceRail(
      [card("its-ticket", { sprint_item_id: "si_one" })],
      [item("si_one", { conversation_id: "conv-supervisor", awaiting_reply: true })]
    );

    expect(rail.items[0].mark).toBe("attention");
  });

  it("leaves an Item its conversation has never spoken on unlit", () => {
    const rail = buildWorkspaceRail(
      [card("its-ticket", { sprint_item_id: "si_one" })],
      [item("si_one", { conversation_id: "conv-supervisor" })]
    );

    expect(rail.items[0].mark).toBeNull();
  });

  it("says nothing about an Item the board has no summary for", () => {
    const rail = buildWorkspaceRail([card("orphan", { sprint_item_id: "si_gone" })], []);

    expect(rail.items[0].mark).toBeNull();
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

    // The Item keeps its box, but quiet child Tickets do not create headings.
    expect(rail.items[0].title).toBe("Finished outcome");
    expect(rail.items[0].groups).toEqual([]);
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

    // A Blocked Ticket is not done, so the Item is not rested.
    const blocked = buildWorkspaceRail(
      [card("blocked", { ticket_status: "blocked", sprint_item_id: "si_three" })],
      [item("si_three")]
    );
    expect(blocked.items[0].rested).toBe(false);
  });

  it("uses only reply and active-work marks on Ticket rows", () => {
    expect(
      workspaceTicketRowMark(card("reply", { awaiting_reply: true, agent_state: "working" }))
    ).toBe("attention");
    expect(workspaceTicketRowMark(card("approval", { awaiting_approval: true }))).toBeNull();
    expect(workspaceTicketRowMark(card("working", { agent_state: "working" }))).toBe("working");
    expect(workspaceTicketRowMark(card("error", { agent_state: "errored" }))).toBeNull();
    expect(workspaceTicketRowMark(card("idle"))).toBeNull();
  });

  it("uses the filled blue state with labels for each attention row", () => {
    expect(workspaceRowMarkPresentation("attention")).toEqual({
      state: "current-awaiting-approval",
      ariaLabel: "Message"
    });
    expect(workspaceRowMarkPresentation("working")).toEqual({
      state: "current-running",
      ariaLabel: "Agent working"
    });
  });

  it("leaves an Item with only child proposals unmarked", () => {
    expect(
      workspaceSprintItemRowMark(
        item("proposals", {
          ticket_rollup: {
            awaiting_reply: false,
            awaiting_approval: true,
            assigned: true,
            agent_state: "idle"
          }
        })
      )
    ).toBeNull();
  });

  it("marks an Item when one child message awaits a reply", () => {
    expect(
      workspaceSprintItemRowMark(
        item("message", {
          awaiting_approval: true,
          assigned: true,
          agent_state: "working",
          ticket_rollup: {
            awaiting_reply: true,
            awaiting_approval: true,
            assigned: true,
            agent_state: "working"
          }
        })
      )
    ).toBe("attention");
  });

  it("uses active work only when no message awaits a reply", () => {
    expect(
      workspaceSprintItemRowMark(
        item("working", {
          ticket_rollup: {
            awaiting_reply: false,
            awaiting_approval: false,
            assigned: false,
            agent_state: "working"
          }
        })
      )
    ).toBe("working");
    expect(workspaceSprintItemRowMark(item("error", { agent_state: "errored" }))).toBeNull();
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
