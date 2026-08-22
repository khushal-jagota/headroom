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

  it("keeps the owner pointer usable when canonical migration storage fails", async () => {
    const message = mint([{
      piece: "file",
      data: "e30=",
      media_type: "application/json",
      file_name: "facts.json"
    }]);
    const ownerId = "owner:ticket:t-migration-failed";
    expect(outgoing.reserveOutgoingMessageFiles(message)).toBe(true);
    expect(await outgoing.rememberOutgoingMessages(ownerId, [message])).toBe(true);
    const setItem = storage.setItem.bind(storage);
    vi.spyOn(storage, "setItem").mockImplementation((key, value) => {
      if (key === "panels.conversation.outgoing.canonical") {
        throw new Error("sessionStorage write failed");
      }
      setItem(key, value);
    });

    expect(await outgoing.moveRememberedOutgoingMessages(ownerId, "canonical")).toBe(false);
    expect(storage.getItem(`panels.conversation.outgoing.${ownerId}`)).not.toBeNull();
    expect(storage.getItem("panels.conversation.outgoing.canonical")).toBeNull();
    expect((await outgoing.recallOutgoingMessages(ownerId))[0].content).toEqual(message.content);

    expect(await outgoing.rememberOutgoingMessages(ownerId, [])).toBe(true);
    expect(storage.getItem(`panels.conversation.outgoing.${ownerId}`)).toBeNull();
    expect(fileEntries.size).toBe(0);
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

  it("stores and recalls a concrete three-MiB image inside the tab envelope", async () => {
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

    await outgoing.rememberOutgoingMessages("boundary", [message]);

    const stored = storage.getItem("panels.conversation.outgoing.boundary");
    expect(stored).not.toBeNull();
    expect(stored!.length).toBeLessThan(5_000_000);
    expect((await outgoing.recallOutgoingMessages("boundary"))[0].content).toEqual(message.content);
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

  it("rebuilds the shared image budget after reload and releases canonical copies", async () => {
    const twoMiB = 2 * 1024 * 1024;
    const base64 = "A".repeat(4 * Math.ceil(twoMiB / 3));
    const first = mint([{ piece: "image", data: base64, media_type: "image/png" }]);
    const second = mint(
      [{ piece: "image", data: base64, media_type: "image/webp" }],
      1_700_000_000_456
    );

    expect(outgoing.reserveOutgoingMessageImages(first)).toBe(true);
    await outgoing.rememberOutgoingMessages("first-conversation", [first]);

    await reloadOutgoingModule();
    outgoing.setOutgoingMessageFileStoreForTest({
      read: async (conversationId) => fileEntries.get(conversationId),
      write: async (conversationId, messages) => { fileEntries.set(conversationId, messages); },
      remove: async (conversationId) => { fileEntries.delete(conversationId); }
    });

    expect(outgoing.reserveOutgoingMessageImages(second)).toBe(false);

    await outgoing.rememberOutgoingMessages("first-conversation", []);
    expect(outgoing.reserveOutgoingMessageImages(second)).toBe(true);
    await outgoing.rememberOutgoingMessages("second-conversation", [second]);

    await reloadOutgoingModule();
    outgoing.setOutgoingMessageFileStoreForTest({
      read: async (conversationId) => fileEntries.get(conversationId),
      write: async (conversationId, messages) => { fileEntries.set(conversationId, messages); },
      remove: async (conversationId) => { fileEntries.delete(conversationId); }
    });

    expect((await outgoing.recallOutgoingMessages("second-conversation"))[0].content)
      .toEqual(second.content);
    await outgoing.rememberOutgoingMessages("second-conversation", []);
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
    expect(await outgoing.recallOutgoingMessages("second-conversation")).toEqual([]);
  });

  it("removes a Conversation key when nothing remains", async () => {
    const message = mint([{ piece: "text", text: "held" }]);
    await outgoing.rememberOutgoingMessages("conversation-1", [message]);

    await outgoing.rememberOutgoingMessages("conversation-1", []);

    expect(storage.getItem("panels.conversation.outgoing.conversation-1")).toBeNull();
    expect(await outgoing.recallOutgoingMessages("conversation-1")).toEqual([]);
  });

  it("keeps a separate ten-MiB envelope for outstanding files", () => {
    const sixMiB = 6 * 1024 * 1024;
    const base64 = "A".repeat(4 * Math.ceil(sixMiB / 3));
    const first = mint([{
      piece: "file",
      data: base64,
      media_type: "text/plain",
      file_name: "first.txt"
    }]);
    const second = mint([{
      piece: "file",
      data: base64,
      media_type: "text/plain",
      file_name: "second.txt"
    }], 1_700_000_000_456);

    expect(outgoing.reserveOutgoingMessageFiles(first)).toBe(true);
    expect(outgoing.reserveOutgoingMessageFiles(second)).toBe(false);
    outgoing.releaseOutgoingMessageFiles(first.messageId);
    expect(outgoing.reserveOutgoingMessageFiles(second)).toBe(true);
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

  it("does not restore stale reservations when IndexedDB delete cleanup fails", async () => {
    outgoing.setOutgoingMessageFileStoreForTest({
      read: async (storageKey) => fileEntries.get(storageKey),
      write: async (storageKey, messages) => { fileEntries.set(storageKey, messages); },
      remove: async () => { throw new Error("delete failed"); }
    });
    const tenMiB = 10 * 1024 * 1024;
    const data = `${"A".repeat(4 * Math.floor(tenMiB / 3))}AA==`;
    const first = mint([{
      piece: "file",
      data,
      media_type: "text/plain",
      file_name: "first.txt"
    }]);
    const second = mint([{
      piece: "file",
      data,
      media_type: "text/plain",
      file_name: "second.txt"
    }], 1_700_000_000_456);

    expect(outgoing.reserveOutgoingMessageFiles(first)).toBe(true);
    expect(await outgoing.rememberOutgoingMessages("delete-failed", [first])).toBe(true);
    expect(await outgoing.rememberOutgoingMessages("delete-failed", [])).toBe(true);
    expect(storage.getItem("panels.conversation.outgoing.delete-failed")).toBeNull();
    expect(fileEntries.size).toBe(1);
    expect(outgoing.reserveOutgoingMessageFiles(second)).toBe(true);
  });
});
