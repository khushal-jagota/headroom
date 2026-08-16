// Where everything stands, and why none of it ever moves.
//
// The world is a place the human learns: the pad they clicked yesterday is where
// they left it, the island they recognise is on the same water. So this is written
// down once and only ever added to. A Sprint Item that arrives is given ground no
// pad is standing on; every pad already placed keeps the exact position and size it
// was drawn at. An island only ever grows outward, and keeps the sea position it
// was first given.
//
// A pad whose Item has left the board keeps its ground for the rest of the session.
// If it were handed back, the next Item would appear where the human last saw a
// different one, and the world would read as though it had shuffled.
//
// Nothing here draws anything. It is arithmetic over ids and distances, so it can
// be read, reasoned about and tested on its own.

import { LAYOUT } from "./tuning";

// What the layout needs to know about a Sprint Item: how big its pad must be.
export type LayoutItem = {
  id: string;
  liveTicketCount: number;
  ticketCount: number;
};

// A pad's ground on its island: where it stands and how far it reaches.
export type PlacedPad = {
  x: number;
  z: number;
  padR: number;
};

export type IslandLayout = {
  // every pad ever placed on this island, in the order it was placed, including
  // the pads of Items that have since left
  pads: ReadonlyMap<string, PlacedPad>;
  radius: number;
  // how many times this island's ground has been rebuilt larger. The first drawing
  // is generation 0; the scatter of each later generation is seeded from it, so the
  // same growth twice grows the same trees.
  generation: number;
};

// An island nobody has seen yet: no pads, no ground, no shore.
export const UNPLACED_ISLAND: IslandLayout = { pads: new Map(), radius: 0, generation: 0 };

// A pad is big enough for its crew, and never smaller than the settled minimum.
export function padRadiusFor(item: LayoutItem): number {
  return Math.max(
    LAYOUT.padMin,
    LAYOUT.padBase +
      item.liveTicketCount * LAYOUT.padPerLiveTicket +
      Math.min(LAYOUT.padPerTicketCap, item.ticketCount * LAYOUT.padPerTicket)
  );
}

// The outward search for ground no pad is standing on: rings of steadily larger
// radius, each read from the island's front and around. The first place that
// clears every pad already down is the one taken — so a new pad settles as close in
// as it can, and the island grows as little as it must.
const SEARCH = {
  ringStep: 0.5,
  ringSlots: 24,
  firstAngle: -Math.PI / 2,
  maxRadius: 600
};

function clearsEveryPad(pads: Iterable<PlacedPad>, x: number, z: number, padR: number): boolean {
  for (const pad of pads) {
    if (Math.hypot(x - pad.x, z - pad.z) < pad.padR + padR + LAYOUT.padGap) return false;
  }
  return true;
}

function placePad(pads: ReadonlyMap<string, PlacedPad>, padR: number): PlacedPad {
  if (pads.size === 0) return { x: 0, z: 0, padR };
  const standing = [...pads.values()];
  const rings = Math.ceil(SEARCH.maxRadius / SEARCH.ringStep);
  for (let ring = 1; ring <= rings; ring++) {
    const r = ring * SEARCH.ringStep;
    for (let slot = 0; slot < SEARCH.ringSlots; slot++) {
      const a = SEARCH.firstAngle + (slot / SEARCH.ringSlots) * Math.PI * 2;
      const x = Math.cos(a) * r;
      const z = Math.sin(a) * r;
      if (clearsEveryPad(standing, x, z, padR)) return { x, z, padR };
    }
  }
  // an island that large is not reachable from a board; still, answer with ground
  // clear of everything rather than ground something is standing on
  let out = 0;
  for (const pad of standing) out = Math.max(out, Math.hypot(pad.x, pad.z) + pad.padR);
  return { x: 0, z: -(out + padR + LAYOUT.padGap), padR };
}

// The Items an island is drawn for the very first time are set out together, on the
// ring the world has always used: one in the middle, or a spread that keeps the
// island round and its pads well apart. Nothing is standing yet, so nothing can be
// disturbed — and a fresh reading of an unchanged board draws the island the reader
// already knows. Only Items that arrive afterwards take the outward search.
function seedPads(items: readonly LayoutItem[]): Map<string, PlacedPad> {
  const pads = new Map<string, PlacedPad>();
  const radii = items.map(padRadiusFor);
  const widest = Math.max(...radii, LAYOUT.padMin);
  const count = items.length;
  items.forEach((item, i) => {
    if (pads.has(item.id)) return;
    if (count === 1) {
      pads.set(item.id, { x: 0, z: 0, padR: radii[i] });
      return;
    }
    const ring =
      count === 2
        ? widest + LAYOUT.ringTwo
        : widest * LAYOUT.ringSpread + count * LAYOUT.ringPerItem;
    const angle = -Math.PI / 2 + (i / count) * Math.PI * 2;
    pads.set(item.id, {
      x: Math.cos(angle) * ring * (count === 2 ? 0.55 : 0.92),
      z: Math.sin(angle) * ring * 0.78,
      padR: radii[i]
    });
  });
  return pads;
}

// A fresh reading of the board, laid over what the island already is. Pads already
// placed are returned untouched; only Items with no ground yet are given any, and
// the radius only ever grows.
export function growIslandLayout(
  previous: IslandLayout,
  items: readonly LayoutItem[]
): IslandLayout {
  let pads: Map<string, PlacedPad> | null = null;
  if (previous.pads.size === 0 && items.length > 0) {
    pads = seedPads(items);
  } else {
    for (const item of items) {
      if (previous.pads.has(item.id)) continue;
      if (pads?.has(item.id)) continue;
      pads ??= new Map(previous.pads);
      pads.set(item.id, placePad(pads, padRadiusFor(item)));
    }
  }
  if (!pads) return previous;

  let reach = 0;
  for (const pad of pads.values()) reach = Math.max(reach, Math.hypot(pad.x, pad.z) + pad.padR);
  const radius = Math.max(previous.radius, reach + LAYOUT.shoreMargin);
  const firstDrawing = previous.radius === 0;
  return {
    pads,
    radius,
    generation: firstDrawing
      ? 0
      : previous.generation + (radius > previous.radius ? 1 : 0)
  };
}

// ---------------- the archipelago ----------------

export type IslandPlacement = {
  // the sea position this project was given the first time it was seen, and keeps
  x: number;
  z: number;
  rot: number;
  // how far its ground reaches now, so a later project appends beyond it
  radius: number;
};

export type IslandRadius = { id: string; radius: number };

function stagger(order: number, radius: number): { z: number; rot: number } {
  const side = order % 2 === 0 ? 1 : -1;
  return {
    z: side * Math.min(LAYOUT.islandStagger, radius * LAYOUT.islandStaggerFraction),
    rot: order % 2 === 0 ? 0.1 : -0.14
  };
}

// Islands are large and far apart, laid along a line. The first reading centres the
// line on the world; after that a placement is never taken back, so a project that
// arrives later appends beyond the far end and an island that grows grows in place.
// Growth can only narrow the water between two islands, never move either of them.
export function placeIslands(
  previous: ReadonlyMap<string, IslandPlacement>,
  islands: readonly IslandRadius[]
): { placements: ReadonlyMap<string, IslandPlacement>; extent: number } {
  const placements = new Map(previous);
  const fresh = islands.filter((island) => !placements.has(island.id));

  if (fresh.length && placements.size === 0) {
    let x = 0;
    fresh.forEach((island, i) => {
      if (i > 0) x += fresh[i - 1].radius + island.radius + LAYOUT.islandGap;
      placements.set(island.id, { x, ...stagger(i, island.radius), radius: island.radius });
    });
    const first = placements.get(fresh[0].id);
    const last = placements.get(fresh[fresh.length - 1].id);
    const cx = ((first?.x ?? 0) + (last?.x ?? 0)) / 2;
    for (const [id, placement] of placements) {
      placements.set(id, { ...placement, x: placement.x - cx });
    }
  } else if (fresh.length) {
    let frontier = 0;
    for (const placement of placements.values()) {
      frontier = Math.max(frontier, placement.x + placement.radius);
    }
    for (const island of fresh) {
      const x = frontier + LAYOUT.islandGap + island.radius;
      placements.set(island.id, {
        x,
        ...stagger(placements.size, island.radius),
        radius: island.radius
      });
      frontier = x + island.radius;
    }
  }

  let extent = 0;
  for (const island of islands) {
    const placement = placements.get(island.id);
    if (!placement) continue;
    if (island.radius > placement.radius) {
      placements.set(island.id, { ...placement, radius: island.radius });
    }
    extent = Math.max(extent, Math.abs(placement.x) + island.radius);
  }
  return { placements, extent };
}
