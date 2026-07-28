import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OutgoingMessage } from "../src/lib/conversation/outgoing";

type OutgoingModule = typeof import("../src/lib/conversation/outgoing");

class MemoryStorage implements Storage {
  readonly entries = new Map<string, string>();

  get length(): number {
    return this.entries.size;
  }

  clear(): void {
    this.entries.clear();
  }

  getItem(key: string): string | null {
    return this.entries.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.entries.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }

  setItem(key: string, value: string): void {
    const next = new Map(this.entries);
    next.set(key, String(value));
    const characters = [...next].reduce(
      (total, [storedKey, storedValue]) => total + storedKey.length + storedValue.length,
      0
    );
    if (characters > 5_000_000) throw new Error("sessionStorage quota exceeded");
    this.entries.set(key, String(value));
  }
}

let storage: MemoryStorage;
let outgoing: OutgoingModule;

function mint(
  content: OutgoingMessage["content"],
  sentAtUnixMilliseconds = 1_700_000_000_123
): OutgoingMessage {
  return outgoing.mintOutgoingMessage({
    content,
    senderLabel: "owner",
    mode: "run_when_free",
    sentAtUnixMilliseconds
  });
}

async function reloadOutgoingModule(): Promise<void> {
  vi.resetModules();
  outgoing = await import("../src/lib/conversation/outgoing");
}

beforeEach(async () => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { sessionStorage: storage });
  await reloadOutgoingModule();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("outgoing Conversation message storage", () => {
  it("recalls each Conversation's complete messages and image bytes", () => {
    const held = {
      ...mint([
        { piece: "text", text: "held" },
        {
          piece: "image",
          data: "AQID",
          media_type: "image/png",
          file_name: "held.png"
        }
      ]),
      knownFate: "waiting_for_the_agent" as const
    };
    const inFlight = mint([{ piece: "text", text: "in flight" }], 1_700_000_000_456);

    outgoing.rememberOutgoingMessages("conversation-1", [held, inFlight]);

    expect(outgoing.recallOutgoingMessages("conversation-1")).toEqual([
      held,
      { ...inFlight, knownFate: "sent_before_this_page" }
    ]);
    expect(outgoing.recallOutgoingMessages("conversation-2")).toEqual([]);
    expect(storage.entries.has("panels.conversation.outgoing.conversation-1")).toBe(true);
  });

  it("rejects untrusted or malformed tab storage", () => {
    storage.setItem(
      "panels.conversation.outgoing.invalid-entries",
      '[{"messageId":"x"},null,7,{"content":[{"piece":"text","text":"no id"}]}]'
    );
    storage.setItem("panels.conversation.outgoing.invalid-json", "not json");

    const image = {
      ...mint([{ piece: "image", data: "AQID", media_type: "image/png" }]),
      content: [{ piece: "image", data: "AQID", media_type: "text/plain" }]
    };
    storage.setItem(
      "panels.conversation.outgoing.invalid-image",
      JSON.stringify([image])
    );

    expect(outgoing.recallOutgoingMessages("invalid-entries")).toEqual([]);
    expect(outgoing.recallOutgoingMessages("invalid-json")).toEqual([]);
    expect(outgoing.recallOutgoingMessages("invalid-image")).toEqual([]);
  });

  it("stores and recalls a concrete three-MiB image inside the tab envelope", () => {
    const threeMiB = 3 * 1024 * 1024;
    const base64 = "A".repeat(4 * Math.ceil(threeMiB / 3));
    const message = mint([
      {
        piece: "image",
        data: base64,
        media_type: "image/png",
        file_name: "boundary.png"
      }
    ]);

    outgoing.rememberOutgoingMessages("boundary", [message]);

    const stored = storage.getItem("panels.conversation.outgoing.boundary");
    expect(stored).not.toBeNull();
    expect(stored!.length).toBeLessThan(5_000_000);
    expect(outgoing.recallOutgoingMessages("boundary")[0].content).toEqual(message.content);
  });

  it("rebuilds the shared image budget after reload and releases canonical copies", async () => {
    const twoMiB = 2 * 1024 * 1024;
    const base64 = "A".repeat(4 * Math.ceil(twoMiB / 3));
    const first = mint([{ piece: "image", data: base64, media_type: "image/png" }]);
    const second = mint(
      [{ piece: "image", data: base64, media_type: "image/webp" }],
      1_700_000_000_456
    );

    expect(outgoing.reserveOutgoingMessageImages(first)).toBe(true);
    outgoing.rememberOutgoingMessages("first-conversation", [first]);

    await reloadOutgoingModule();

    expect(outgoing.reserveOutgoingMessageImages(second)).toBe(false);

    outgoing.rememberOutgoingMessages("first-conversation", []);
    expect(outgoing.reserveOutgoingMessageImages(second)).toBe(true);
    outgoing.rememberOutgoingMessages("second-conversation", [second]);

    await reloadOutgoingModule();

    expect(outgoing.recallOutgoingMessages("second-conversation")[0].content)
      .toEqual(second.content);
    outgoing.rememberOutgoingMessages("second-conversation", []);
    outgoing.releaseOutgoingMessageImages(second.messageId);

    const threeMiB = 3 * 1024 * 1024;
    const finalMessage = mint([
      {
        piece: "image",
        data: "A".repeat(4 * Math.ceil(threeMiB / 3)),
        media_type: "image/gif"
      }
    ], 1_700_000_000_789);
    expect(outgoing.reserveOutgoingMessageImages(finalMessage)).toBe(true);
    expect(outgoing.recallOutgoingMessages("second-conversation")).toEqual([]);
  });

  it("removes a Conversation key when nothing remains", () => {
    const message = mint([{ piece: "text", text: "held" }]);
    outgoing.rememberOutgoingMessages("conversation-1", [message]);

    outgoing.rememberOutgoingMessages("conversation-1", []);

    expect(storage.getItem("panels.conversation.outgoing.conversation-1")).toBeNull();
    expect(outgoing.recallOutgoingMessages("conversation-1")).toEqual([]);
  });
});
