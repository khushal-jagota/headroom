import { describe, expect, it } from "vitest";

import {
  activityForStage,
  buildAtlasWorld,
  findAtlasItem,
  findAtlasTicket,
  happeningsBetween,
  UNSCHEDULED_ITEM_TITLE
} from "../src/lib/atlas/model";
import {
  createSlotAllocator,
  readSlotBook,
  SLOT_STORAGE_KEY,
  writeSlotBook,
  type SlotBook
} from "../src/lib/atlas/slots";
import type { BoardCard, BoardResponse, BoardSprintItem, ReviewResponse } from "../src/lib/types";
import { boardCard as card } from "./boardCardFixture";

function board(columns: Array<[string, BoardCard[]]>, items: BoardSprintItem[] = []): BoardResponse {
  return {
    columns: columns.map(([stage, cards]) => ({ stage, cards })),
    sprint_items: items
  };
}

function sprintItem(id: string, values: Partial<BoardSprintItem> = {}): BoardSprintItem {
  return {
    id,
    created_at: 0,
    conversation_id: null,
    agent_working: false,
    needs_me: false,
    latest_ping_sequence: 0,
    ...values
  };
}

function emptyBook(): SlotBook {
  return { projects: {}, items: {}, tickets: {} };
}

describe("the world's view of the board", () => {
  it("makes an island per project and a pad per Sprint Item", () => {
    const { world } = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("a", { project_id: "p1", project: "Panels", sprint_item_id: "i1", sprint_item_title: "Atlas" }),
            card("b", { project_id: "p1", project: "Panels", sprint_item_id: "i1", sprint_item_title: "Atlas" }),
            card("c", { project_id: "p2", project: "Vylo", sprint_item_id: "i2", sprint_item_title: "Launch" })
          ]
        ]
      ]),
      undefined,
      emptyBook()
    );

    expect(world.projects.map((project) => project.name)).toEqual(["Panels", "Vylo"]);
    expect(world.projects[0].items).toHaveLength(1);
    expect(world.projects[0].items[0].tickets.map((ticket) => ticket.id)).toEqual(["a", "b"]);
    expect(world.projects[1].items[0].title).toBe("Launch");
  });

  it("gives a Ticket with no Sprint Item a pad of its own rather than dropping it", () => {
    const { world } = buildAtlasWorld(
      board([["needs_plan", [card("a", { project_id: "p1", project: "Panels" })]]]),
      undefined,
      emptyBook()
    );

    expect(world.projects[0].items[0].title).toBe(UNSCHEDULED_ITEM_TITLE);
    expect(world.projects[0].items[0].tickets).toHaveLength(1);
  });

  it("leaves dropped Tickets out of the world", () => {
    const { world } = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("a", { project_id: "p1", project: "Panels" }),
            card("gone", { project_id: "p1", project: "Panels", is_dropped: true })
          ]
        ]
      ]),
      undefined,
      emptyBook()
    );

    expect(world.projects[0].items[0].tickets.map((ticket) => ticket.id)).toEqual(["a"]);
  });

  it("reads progress from the board's own column order and nothing else", () => {
    const { world } = buildAtlasWorld(
      board([
        ["needs_success", [card("early", { project_id: "p1", stage: "needs_success" })]],
        ["needs_closeout", [card("late", { project_id: "p1", stage: "needs_closeout" })]],
        ["done", [card("finished", { project_id: "p1", stage: "done", is_done: true })]]
      ]),
      undefined,
      emptyBook()
    );

    const tickets = world.projects[0].items[0].tickets;
    const early = tickets.find((ticket) => ticket.id === "early")!;
    const late = tickets.find((ticket) => ticket.id === "late")!;
    const finished = tickets.find((ticket) => ticket.id === "finished")!;

    expect(early.frac).toBeLessThan(late.frac);
    expect(finished.frac).toBe(1);
  });

  it("carries each Sprint Item's own supervisor signals", () => {
    const { world } = buildAtlasWorld(
      board(
        [["needs_plan", [card("a", { project_id: "p1", sprint_item_id: "i1" })]]],
        [sprintItem("i1", { agent_working: true, needs_me: true })]
      ),
      undefined,
      emptyBook()
    );

    expect(world.projects[0].items[0].supervisorWorking).toBe(true);
    expect(world.projects[0].items[0].supervisorNeedsMe).toBe(true);
  });

  it("counts an Item finished only when it has work and all of it stands done", () => {
    const { world } = buildAtlasWorld(
      board([
        [
          "done",
          [
            card("a", { project_id: "p1", sprint_item_id: "i1", is_done: true }),
            card("b", { project_id: "p1", sprint_item_id: "i1", is_done: true }),
            card("c", { project_id: "p1", sprint_item_id: "i2", is_done: true }),
            card("d", { project_id: "p1", sprint_item_id: "i2" })
          ]
        ]
      ]),
      undefined,
      emptyBook()
    );

    expect(findAtlasItem(world, "i1")?.allDone).toBe(true);
    expect(findAtlasItem(world, "i2")?.allDone).toBe(false);
  });

  it("dresses the crew by the trade most of them belong to", () => {
    const { world } = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("a", { project_id: "p1", sprint_item_id: "i1", worker_type: "coding" }),
            card("b", { project_id: "p1", sprint_item_id: "i1", worker_type: "coding" }),
            card("c", { project_id: "p1", sprint_item_id: "i1", worker_type: "exploration" })
          ]
        ]
      ]),
      undefined,
      emptyBook()
    );

    expect(findAtlasItem(world, "i1")?.dominantType).toBe("coding");
  });

  it("names trouble by its kind", () => {
    const { world } = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("errored", { project_id: "p1", backend_error: "the backend fell over" }),
            card("blocked", { project_id: "p1", blocked: true }),
            card("well", { project_id: "p1" })
          ]
        ]
      ]),
      undefined,
      emptyBook()
    );

    expect(findAtlasTicket(world, "errored")?.troubleKind).toBe("error");
    expect(findAtlasTicket(world, "blocked")?.troubleKind).toBe("blocked");
    expect(findAtlasTicket(world, "well")?.trouble).toBe(false);
  });

  it("carries the review queue and the running count through untouched", () => {
    const review: ReviewResponse = {
      items: [
        {
          review_item_type: "proposal",
          ticket_id: "a",
          field: "approach",
          title: "A ticket",
          waiting_since: 12
        }
      ],
      running_worker_count: 3
    };
    const { world } = buildAtlasWorld(board([["needs_plan", [card("a")]]]), review, emptyBook());

    expect(world.review).toEqual(review.items);
    expect(world.runningWorkerCount).toBe(3);
  });

  it("reads what a worker is doing from the stage it is on", () => {
    expect(activityForStage("needs_success")).toBe("survey");
    expect(activityForStage("needs_plan")).toBe("measure");
    expect(activityForStage("needs_implementation")).toBe("carve");
    expect(activityForStage("needs_closeout")).toBe("polish");
    expect(activityForStage("")).toBe("carve");
  });
});

describe("where things stand", () => {
  it("holds a place across refreshes even when the board comes back in another order", () => {
    const book = emptyBook();
    const first = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("a", { project_id: "p1", sprint_item_id: "i1" }),
            card("b", { project_id: "p1", sprint_item_id: "i1" })
          ]
        ]
      ]),
      undefined,
      book
    );
    const slotOfA = findAtlasTicket(first.world, "a")!.slot;
    const slotOfB = findAtlasTicket(first.world, "b")!.slot;

    const second = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("b", { project_id: "p1", sprint_item_id: "i1" }),
            card("a", { project_id: "p1", sprint_item_id: "i1" })
          ]
        ]
      ]),
      undefined,
      first.book
    );

    expect(findAtlasTicket(second.world, "a")!.slot).toBe(slotOfA);
    expect(findAtlasTicket(second.world, "b")!.slot).toBe(slotOfB);
    // And the world hands them over in that same standing order, not the board's.
    expect(second.world.projects[0].items[0].tickets.map((ticket) => ticket.id)).toEqual(["a", "b"]);
  });

  it("gives a newcomer the next free place and leaves everybody else where they were", () => {
    const allocator = createSlotAllocator({ projects: {}, items: {}, tickets: { a: 0, b: 1 } });
    expect(allocator.slotFor("tickets", "a")).toBe(0);
    expect(allocator.grew).toBe(false);
    expect(allocator.slotFor("tickets", "c")).toBe(2);
    expect(allocator.grew).toBe(true);
  });

  it("survives a browser with no storage, and one with nonsense in it", () => {
    expect(readSlotBook(null)).toEqual(emptyBook());
    expect(readSlotBook({ getItem: () => "not json" })).toEqual(emptyBook());
    expect(readSlotBook({ getItem: () => JSON.stringify({ tickets: { a: "third" } }) })).toEqual(
      emptyBook()
    );
    expect(() => writeSlotBook(null, emptyBook())).not.toThrow();
  });

  it("reads back what it wrote", () => {
    const kept = new Map<string, string>();
    const storage = {
      getItem: (key: string) => kept.get(key) ?? null,
      setItem: (key: string, value: string) => void kept.set(key, value)
    };
    const book: SlotBook = { projects: { p1: 0 }, items: {}, tickets: { a: 4 } };
    writeSlotBook(storage, book);
    expect(kept.has(SLOT_STORAGE_KEY)).toBe(true);
    expect(readSlotBook(storage)).toEqual(book);
  });
});

describe("what the world speaks", () => {
  const before = buildAtlasWorld(
    board([
      [
        "needs_plan",
        [
          card("finishing", { project_id: "p1", sprint_item_id: "i1" }),
          card("troubled", { project_id: "p1", sprint_item_id: "i1" }),
          card("waiting", { project_id: "p1", sprint_item_id: "i1" }),
          card("moving", { project_id: "p1", sprint_item_id: "i1", stage: "needs_plan" })
        ]
      ]
    ]),
    undefined,
    emptyBook()
  );

  it("says nothing at all on the first reading", () => {
    expect(happeningsBetween(null, before.world)).toEqual([]);
  });

  it("speaks only what actually changed", () => {
    const after = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("finishing", { project_id: "p1", sprint_item_id: "i1", is_done: true }),
            card("troubled", { project_id: "p1", sprint_item_id: "i1", backend_error: "fell over" }),
            card("waiting", { project_id: "p1", sprint_item_id: "i1", needs_me: true }),
            card("moving", {
              project_id: "p1",
              sprint_item_id: "i1",
              stage: "needs_implementation",
              stage_label: "Implementation"
            })
          ]
        ]
      ]),
      undefined,
      before.book
    );

    expect(happeningsBetween(before.world, after.world)).toEqual([
      { ticketId: "finishing", itemId: "i1", text: "Finished!", tone: "done" },
      { ticketId: "troubled", itemId: "i1", text: "Hit trouble", tone: "trouble" },
      { ticketId: "waiting", itemId: "i1", text: "Needs you", tone: "needs" },
      { ticketId: "moving", itemId: "i1", text: "Moved to Implementation", tone: "stage" }
    ]);
  });

  it("lets a Ticket that has just arrived announce nothing", () => {
    const after = buildAtlasWorld(
      board([
        [
          "needs_plan",
          [
            card("finishing", { project_id: "p1", sprint_item_id: "i1" }),
            card("troubled", { project_id: "p1", sprint_item_id: "i1" }),
            card("waiting", { project_id: "p1", sprint_item_id: "i1" }),
            card("moving", { project_id: "p1", sprint_item_id: "i1", stage: "needs_plan" }),
            card("newcomer", { project_id: "p1", sprint_item_id: "i1", needs_me: true })
          ]
        ]
      ]),
      undefined,
      before.book
    );

    expect(happeningsBetween(before.world, after.world)).toEqual([]);
  });
});
