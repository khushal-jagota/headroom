// The world never shuffles.
//
// An island's ground is written down once and only ever added to: a pad the human
// has seen keeps its exact place and size for as long as the screen is open, the
// island only ever grows outward, and an island keeps the sea it was first given.
// These are the rules the drawing depends on, proved here without a browser.

import { describe, expect, it } from "vitest";

import {
  growIslandLayout,
  padRadiusFor,
  placeIslands,
  UNPLACED_ISLAND,
  type IslandLayout,
  type IslandPlacement,
  type LayoutItem
} from "../src/lib/atlas/world/layout";
import { LAYOUT } from "../src/lib/atlas/world/tuning";

function item(id: string, liveTicketCount = 1, ticketCount = 1): LayoutItem {
  return { id, liveTicketCount, ticketCount };
}

function padOf(layout: IslandLayout, id: string): { x: number; z: number; padR: number } {
  const pad = layout.pads.get(id);
  if (!pad) throw new Error(`no pad for ${id}`);
  return pad;
}

function everyPairClears(layout: IslandLayout): void {
  const pads = [...layout.pads.entries()];
  for (let i = 0; i < pads.length; i++) {
    for (let j = i + 1; j < pads.length; j++) {
      const [aId, a] = pads[i];
      const [bId, b] = pads[j];
      const between = Math.hypot(a.x - b.x, a.z - b.z);
      expect(
        between,
        `${aId} and ${bId} are ${between.toFixed(2)} apart`
      ).toBeGreaterThanOrEqual(a.padR + b.padR + LAYOUT.padGap - 1e-9);
    }
  }
}

function containsEveryPad(layout: IslandLayout): void {
  for (const [id, pad] of layout.pads) {
    expect(Math.hypot(pad.x, pad.z) + pad.padR + LAYOUT.shoreMargin, id).toBeLessThanOrEqual(
      layout.radius + 1e-9
    );
  }
}

describe("an island's layout", () => {
  it("gives the first item the middle of the island", () => {
    const layout = growIslandLayout(UNPLACED_ISLAND, [item("a")]);
    expect(padOf(layout, "a")).toEqual({ x: 0, z: 0, padR: padRadiusFor(item("a")) });
    expect(layout.radius).toBeCloseTo(padRadiusFor(item("a")) + LAYOUT.shoreMargin, 10);
    expect(layout.generation).toBe(0);
  });

  it("leaves every pad it has already placed exactly where it stands", () => {
    let layout = growIslandLayout(UNPLACED_ISLAND, [item("a"), item("b"), item("c")]);
    const before = new Map([...layout.pads].map(([id, pad]) => [id, { ...pad }]));

    for (const arrival of ["d", "e", "f", "g", "h"]) {
      layout = growIslandLayout(layout, [
        ...[...before.keys()].map((id) => item(id)),
        item(arrival)
      ]);
      for (const [id, pad] of before) expect(padOf(layout, id)).toEqual(pad);
      before.set(arrival, { ...padOf(layout, arrival) });
      everyPairClears(layout);
      containsEveryPad(layout);
    }
  });

  it("keeps a pad's size even when its item grows a crew", () => {
    const first = growIslandLayout(UNPLACED_ISLAND, [item("a", 1, 1)]);
    const later = growIslandLayout(first, [item("a", 9, 12)]);
    expect(later).toBe(first);
    expect(padOf(later, "a").padR).toBe(padOf(first, "a").padR);
  });

  it("only ever grows its radius, one generation per growth", () => {
    let layout = growIslandLayout(UNPLACED_ISLAND, [item("a")]);
    let radius = layout.radius;
    let generation = layout.generation;
    for (const arrival of ["b", "c", "d", "e", "f", "g", "h", "i"]) {
      const ids = [...layout.pads.keys(), arrival];
      layout = growIslandLayout(layout, ids.map((id) => item(id)));
      expect(layout.radius).toBeGreaterThanOrEqual(radius);
      expect(layout.generation).toBeGreaterThanOrEqual(generation);
      expect(layout.generation - generation).toBeLessThanOrEqual(1);
      if (layout.radius > radius) expect(layout.generation).toBe(generation + 1);
      radius = layout.radius;
      generation = layout.generation;
    }
    // a reading that adds nothing changes nothing at all
    const settled = growIslandLayout(layout, [...layout.pads.keys()].map((id) => item(id)));
    expect(settled).toBe(layout);
  });

  it("never hands a departed item's ground to a newcomer", () => {
    const three = growIslandLayout(UNPLACED_ISLAND, [item("a"), item("b"), item("c")]);
    const departed = padOf(three, "b");

    // "b" leaves the board, and later "d" arrives in its place on the reading
    const without = growIslandLayout(three, [item("a"), item("c")]);
    expect(without).toBe(three);
    const after = growIslandLayout(without, [item("a"), item("c"), item("d")]);

    const arrived = padOf(after, "d");
    expect(arrived).not.toEqual(departed);
    expect(Math.hypot(arrived.x - departed.x, arrived.z - departed.z)).toBeGreaterThanOrEqual(
      arrived.padR + departed.padR + LAYOUT.padGap - 1e-9
    );
    // and "b" keeps its ground, so it comes back to where it was
    expect(padOf(after, "b")).toEqual(departed);
    everyPairClears(after);
  });

  it("draws the same layout from the same readings", () => {
    const readings = [
      [item("a", 2, 3), item("b", 1, 1)],
      [item("a", 2, 3), item("b", 1, 1), item("c", 4, 6)],
      [item("a", 2, 3), item("c", 4, 6), item("d", 1, 9)]
    ];
    const run = (): IslandLayout =>
      readings.reduce((layout, reading) => growIslandLayout(layout, reading), UNPLACED_ISLAND);
    const once = run();
    const twice = run();
    expect([...twice.pads]).toEqual([...once.pads]);
    expect(twice.radius).toBe(once.radius);
    expect(twice.generation).toBe(once.generation);
  });
});

describe("the archipelago", () => {
  const seat = (
    previous: ReadonlyMap<string, IslandPlacement>,
    islands: { id: string; radius: number }[]
  ): { placements: ReadonlyMap<string, IslandPlacement>; extent: number } =>
    placeIslands(previous, islands);

  it("centres the first reading on the world", () => {
    const { placements } = seat(new Map(), [
      { id: "p1", radius: 10 },
      { id: "p2", radius: 10 },
      { id: "p3", radius: 10 }
    ]);
    const xs = [...placements.values()].map((placement) => placement.x);
    expect(xs[0] + xs[2]).toBeCloseTo(0, 10);
    expect(xs[1]).toBeCloseTo(0, 10);
    expect(xs[1] - xs[0]).toBeCloseTo(20 + LAYOUT.islandGap, 10);
  });

  it("holds every island still while one of them grows", () => {
    const first = seat(new Map(), [
      { id: "p1", radius: 10 },
      { id: "p2", radius: 12 }
    ]);
    const grown = seat(first.placements, [
      { id: "p1", radius: 26 },
      { id: "p2", radius: 12 }
    ]);
    for (const [id, placement] of first.placements) {
      const after = grown.placements.get(id);
      expect(after?.x).toBe(placement.x);
      expect(after?.z).toBe(placement.z);
      expect(after?.rot).toBe(placement.rot);
    }
    // the world is wider than it was, so the framing and the haze still fit it
    expect(grown.extent).toBeGreaterThan(first.extent);
  });

  it("appends a new project beyond the far end, moving nobody", () => {
    const first = seat(new Map(), [
      { id: "p1", radius: 10 },
      { id: "p2", radius: 12 }
    ]);
    const grown = seat(first.placements, [
      { id: "p1", radius: 26 },
      { id: "p2", radius: 12 }
    ]);
    const withNew = seat(grown.placements, [
      { id: "p1", radius: 26 },
      { id: "p2", radius: 12 },
      { id: "p3", radius: 9 }
    ]);
    for (const [id, placement] of grown.placements) {
      expect(withNew.placements.get(id)?.x).toBe(placement.x);
      expect(withNew.placements.get(id)?.z).toBe(placement.z);
    }
    const p2 = withNew.placements.get("p2");
    const p3 = withNew.placements.get("p3");
    expect(p3?.x ?? 0).toBeGreaterThan((p2?.x ?? 0) + 12);
    expect((p3?.x ?? 0) - 9).toBeCloseTo((p2?.x ?? 0) + 12 + LAYOUT.islandGap, 10);
  });

  it("keeps a departed project's water for it", () => {
    const first = seat(new Map(), [
      { id: "p1", radius: 10 },
      { id: "p2", radius: 12 }
    ]);
    const alone = seat(first.placements, [{ id: "p1", radius: 10 }]);
    expect(alone.placements.get("p2")).toEqual(first.placements.get("p2"));
    const back = seat(alone.placements, [
      { id: "p1", radius: 10 },
      { id: "p2", radius: 12 }
    ]);
    expect(back.placements.get("p2")?.x).toBe(first.placements.get("p2")?.x);
  });
});

// The everyday case is a fresh reading of an unchanged board. That must draw the
// island the reader already knows, so the Items present at first sight are set out
// together on the ring the world has always used — not by the outward search, which
// exists for Items that arrive while somebody is looking.
describe("an island drawn for the first time", () => {
  const three = [
    { id: "a", liveTicketCount: 2, ticketCount: 4 },
    { id: "b", liveTicketCount: 1, ticketCount: 3 },
    { id: "c", liveTicketCount: 3, ticketCount: 6 }
  ];

  it("spreads the Items it already has around the island, none of them at the centre", () => {
    const laid = growIslandLayout(UNPLACED_ISLAND, three);
    for (const item of three) {
      const pad = laid.pads.get(item.id)!;
      expect(Math.hypot(pad.x, pad.z)).toBeGreaterThan(1);
    }
  });

  it("stays as compact as the ring layout it has always used", () => {
    // The outward search seeds the first pad at the centre and pushes the rest
    // fully outboard, which made the island half again as wide for the same work.
    const laid = growIslandLayout(UNPLACED_ISLAND, three);
    expect(laid.radius).toBeLessThan(30);
    expect(laid.generation).toBe(0);
  });

  it("still holds every seeded pad still when a later Item arrives", () => {
    const laid = growIslandLayout(UNPLACED_ISLAND, three);
    const grown = growIslandLayout(laid, [...three, { id: "d", liveTicketCount: 1, ticketCount: 1 }]);
    for (const item of three) {
      expect(grown.pads.get(item.id)).toEqual(laid.pads.get(item.id));
    }
    expect(grown.pads.get("d")).toBeDefined();
    expect(grown.radius).toBeGreaterThanOrEqual(laid.radius);
  });

  it("gives a lone Item the middle of its island, as it always has", () => {
    const laid = growIslandLayout(UNPLACED_ISLAND, [three[0]]);
    expect(laid.pads.get("a")).toMatchObject({ x: 0, z: 0 });
  });
});
