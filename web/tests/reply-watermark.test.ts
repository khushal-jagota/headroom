import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  onReplyWatermarkMoved,
  readReplyWatermark,
  writeReplyWatermark
} from "../src/lib/replyWatermark";

class MemoryStorage implements Storage {
  readonly entries = new Map<string, string>();
  rejectWrites = false;
  rejectReads = false;

  get length(): number {
    return this.entries.size;
  }

  clear(): void {
    this.entries.clear();
  }

  getItem(key: string): string | null {
    if (this.rejectReads) throw new Error("storage is blocked");
    return this.entries.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.entries.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }

  setItem(key: string, value: string): void {
    if (this.rejectWrites) throw new Error("quota exceeded");
    this.entries.set(key, String(value));
  }
}

let storage: MemoryStorage;
let stopListeners: Array<() => void>;

beforeEach(() => {
  storage = new MemoryStorage();
  stopListeners = [];
  vi.stubGlobal("localStorage", storage);
});

afterEach(() => {
  for (const stopListening of stopListeners) stopListening();
  vi.unstubAllGlobals();
});

function listen(listener: () => void): () => void {
  const stopListening = onReplyWatermarkMoved(listener);
  stopListeners.push(stopListening);
  return stopListening;
}

describe("reply watermark persistence", () => {
  it("reads an absent Conversation as zero", () => {
    expect(readReplyWatermark("conv-a")).toBe(0);
  });

  it("persists under a key naming the Conversation and notifies movement", () => {
    const moves: number[] = [];
    listen(() => moves.push(readReplyWatermark("conv-a")));

    writeReplyWatermark("conv-a", 12);

    expect(readReplyWatermark("conv-a")).toBe(12);
    expect(storage.getItem("panels.replySeen.conv-a")).toBe("12");
    expect(moves).toEqual([12]);
  });

  it("does not move or notify for equal, backward, fractional, or NaN writes", () => {
    const moves: number[] = [];
    listen(() => moves.push(readReplyWatermark("conv-a")));
    writeReplyWatermark("conv-a", 12);

    writeReplyWatermark("conv-a", 12);
    writeReplyWatermark("conv-a", 4);
    writeReplyWatermark("conv-a", 2.5);
    writeReplyWatermark("conv-a", Number.NaN);

    expect(readReplyWatermark("conv-a")).toBe(12);
    expect(storage.getItem("panels.replySeen.conv-a")).toBe("12");
    expect(moves).toEqual([12]);
  });

  it.each(["", "  ", "seven", "3.5", "-2", "NaN"])(
    "reads malformed stored value %j as zero",
    (storedValue) => {
      storage.entries.set("panels.replySeen.conv-broken", storedValue);

      expect(readReplyWatermark("conv-broken")).toBe(0);
    }
  );

  it("does not throw or notify when storage refuses a write", () => {
    let movementCount = 0;
    listen(() => {
      movementCount += 1;
    });
    storage.rejectWrites = true;

    expect(() => writeReplyWatermark("conv-full", 9)).not.toThrow();
    expect(readReplyWatermark("conv-full")).toBe(0);
    expect(movementCount).toBe(0);
  });

});
