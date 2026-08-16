// Where a thing stands in the world.
//
// Atlas lays the world out from slot numbers, never from the order the board
// happens to return. A project keeps its island, an item keeps its pad, and a
// worker keeps its place on that pad, for as long as this browser remembers it.
// Positions never shuffle: that is the design rule, and this is the whole of how
// it is kept.
//
// The number is per browser, like how far somebody has read. The server is never
// told, because where the islands sit is about this reader looking at this screen.

export type SlotKind = "projects" | "items" | "tickets";

export type SlotBook = Record<SlotKind, Record<string, number>>;

export const SLOT_STORAGE_KEY = "atlas-world-slots-v1";

function emptyBook(): SlotBook {
  return { projects: {}, items: {}, tickets: {} };
}

// A browser that has never seen this world gives everything a fresh slot, in the
// order it is asked for. That is stable from then on.
export function readSlotBook(storage: Pick<Storage, "getItem"> | null): SlotBook {
  const book = emptyBook();
  if (!storage) return book;
  let stored: unknown;
  try {
    stored = JSON.parse(storage.getItem(SLOT_STORAGE_KEY) || "null");
  } catch {
    return book;
  }
  if (!stored || typeof stored !== "object") return book;
  for (const kind of ["projects", "items", "tickets"] as const) {
    const kept = (stored as Partial<SlotBook>)[kind];
    if (!kept || typeof kept !== "object") continue;
    for (const [id, slot] of Object.entries(kept)) {
      if (typeof slot === "number" && Number.isFinite(slot)) book[kind][id] = slot;
    }
  }
  return book;
}

// The allocator hands out the next free number and reports whether it had to,
// so the caller writes storage once per refresh instead of once per thing.
export function createSlotAllocator(book: SlotBook) {
  let grew = false;
  return {
    slotFor(kind: SlotKind, id: string): number {
      const kept = book[kind];
      if (!(id in kept)) {
        let highest = -1;
        for (const slot of Object.values(kept)) highest = Math.max(highest, slot);
        kept[id] = highest + 1;
        grew = true;
      }
      return kept[id];
    },
    get book(): SlotBook {
      return book;
    },
    get grew(): boolean {
      return grew;
    }
  };
}

export function writeSlotBook(storage: Pick<Storage, "setItem"> | null, book: SlotBook): void {
  if (!storage) return;
  try {
    storage.setItem(SLOT_STORAGE_KEY, JSON.stringify(book));
  } catch {
    // A browser that refuses storage still gets a coherent world for this visit.
    // It only forgets where things stood the next time it loads.
  }
}
