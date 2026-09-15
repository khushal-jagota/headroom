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
let fileEntries: Map<string, unknown>;

function mint(
  content: OutgoingMessage["content"],
  sentAtUnixMilliseconds = 1_700_000_000_123
): OutgoingMessage {
  return outgoing.mintOutgoingMessage({
    content,
    senderLabel: "owner",
    mode: "queue",
    sentAtUnixMilliseconds
  });
}

async function reloadOutgoingModule(): Promise<void> {
  vi.resetModules();
  outgoing = await import("../src/lib/conversation/outgoing");
}

beforeEach(async () => {
  storage = new MemoryStorage();
  fileEntries = new Map();
  vi.stubGlobal("window", { sessionStorage: storage });
  await reloadOutgoingModule();
  outgoing.setOutgoingMessageFileStoreForTest({
    read: async (conversationId) => fileEntries.get(conversationId),
    write: async (conversationId, messages) => {
      fileEntries.set(conversationId, messages);
    },
    remove: async (conversationId) => {
      fileEntries.delete(conversationId);
    }
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("outgoing Conversation message storage", () => {
  it("uses the intended Conversation id before a first message opens it", async () => {
    const message = mint([{
      piece: "file",
      data: "e30=",
      media_type: "application/json",
      file_name: "facts.json"
    }]);
    const persistenceId = outgoing.outgoingPersistenceConversationId(
      null,
      "intended-conversation",
      message.messageId
    );

    expect(persistenceId).toBe("intended-conversation");
    expect(await outgoing.rememberOutgoingMessages(persistenceId, [message])).toBe(true);
    expect(storage.getItem("panels.conversation.outgoing.intended-conversation"))
      .toContain('"storage":"indexed_db"');
  });

  it("moves an owner pointer to the canonical id after a lost first-send response", async () => {
    const message = mint([{
      piece: "file",
      data: "e30=",
      media_type: "application/json",
      file_name: "facts.json"
    }]);
    const provisionalId = "owner:ticket:t-lost-response";
    await outgoing.rememberOutgoingMessages(provisionalId, [message]);

    expect(await outgoing.moveRememberedOutgoingMessages(provisionalId, "canonical")).toBe(true);
    expect(storage.getItem(`panels.conversation.outgoing.${provisionalId}`)).toBeNull();
    expect((await outgoing.recallOutgoingMessages("canonical"))[0].content)
      .toEqual(message.content);
  });

  it("recalls each Conversation's complete messages and image bytes", async () => {
    const held = {
      ...mint([
        { piece: "text", text: "held" },
        {
          piece: "image",
          data: "AQID",
          media_type: "image/png",
          file_name: "held.png"
        },
        {
          piece: "file",
          data: "e30=",
          media_type: "application/json",
          file_name: "facts.json"
        }
      ]),
      knownFate: "waiting_for_the_agent" as const
    };
    const inFlight = mint([{ piece: "text", text: "in flight" }], 1_700_000_000_456);

    await outgoing.rememberOutgoingMessages("conversation-1", [held, inFlight]);

    expect(await outgoing.recallOutgoingMessages("conversation-1")).toEqual([
      held,
      { ...inFlight, knownFate: "sent_before_this_page" }
    ]);
    expect(await outgoing.recallOutgoingMessages("conversation-2")).toEqual([]);
    expect(storage.entries.has("panels.conversation.outgoing.conversation-1")).toBe(true);
  });

  it("rejects untrusted or malformed tab storage", async () => {
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
    storage.setItem(
      "panels.conversation.outgoing.invalid-file",
      JSON.stringify([{
        ...mint([{ piece: "file", data: "e30=", media_type: "application/json", file_name: "facts.json" }]),
        content: [{ piece: "file", data: "e30=", media_type: "text/html", file_name: "facts.json" }]
      }])
    );

    expect(await outgoing.recallOutgoingMessages("invalid-entries")).toEqual([]);
    expect(await outgoing.recallOutgoingMessages("invalid-json")).toEqual([]);
    expect(await outgoing.recallOutgoingMessages("invalid-image")).toEqual([]);
    expect(await outgoing.recallOutgoingMessages("invalid-file")).toEqual([]);
  });

  it("stores a ten-MiB file in IndexedDB with compact session coordination", async () => {
    const tenMiB = 10 * 1024 * 1024;
    const message = mint([{
      piece: "file",
      data: `${"A".repeat(4 * Math.floor(tenMiB / 3))}AA==`,
      media_type: "text/plain",
      file_name: "boundary.txt"
    }]);

    await outgoing.rememberOutgoingMessages("file-boundary", [message]);

    const coordinated = storage.getItem("panels.conversation.outgoing.file-boundary");
    expect(coordinated).toContain('"storage":"indexed_db"');
    expect(coordinated!.length).toBeLessThan(1_000);
    expect([...fileEntries.values()]).toEqual([[message]]);

    await reloadOutgoingModule();
    outgoing.setOutgoingMessageFileStoreForTest({
      read: async (conversationId) => fileEntries.get(conversationId),
      write: async (conversationId, messages) => { fileEntries.set(conversationId, messages); },
      remove: async (conversationId) => { fileEntries.delete(conversationId); }
    });

    expect((await outgoing.recallOutgoingMessages("file-boundary"))[0].content)
      .toEqual(message.content);
  });

  it("reports an IndexedDB open or write failure before a file send proceeds", async () => {
    outgoing.setOutgoingMessageFileStoreForTest({
      read: async () => undefined,
      write: async () => { throw new Error("IndexedDB did not open"); },
      remove: async () => undefined
    });
    const message = mint([{
      piece: "file",
      data: "e30=",
      media_type: "application/json",
      file_name: "facts.json"
    }]);

    expect(await outgoing.rememberOutgoingMessages("write-failed", [message])).toBe(false);
    expect(storage.getItem("panels.conversation.outgoing.write-failed")).toBeNull();
    expect(await outgoing.recallOutgoingMessages("write-failed")).toEqual([]);
  });

});
