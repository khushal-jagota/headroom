import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

// replyWatermark.ts imports nothing, so transpiling it alone is enough to import it.
const source = await readFile(new URL("../src/lib/replyWatermark.ts", import.meta.url), "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;

const dir = await mkdtemp(join(tmpdir(), "panels-reply-watermark-"));
const modulePath = join(dir, "replyWatermark.mjs");
await writeFile(modulePath, transpiled, "utf8");

class MemoryStorage {
  constructor() {
    this.entries = new Map();
    this.rejectWrites = false;
  }

  getItem(key) {
    return this.entries.has(key) ? this.entries.get(key) : null;
  }

  setItem(key, value) {
    if (this.rejectWrites) throw new Error("quota exceeded");
    this.entries.set(key, String(value));
  }
}

const storage = new MemoryStorage();
globalThis.localStorage = storage;

const { readReplyWatermark, writeReplyWatermark } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

// A conversation nobody has read is unread.
assert.equal(readReplyWatermark("conv-a"), 0);

// A write is readable back, under a key naming the conversation.
writeReplyWatermark("conv-a", 12);
assert.equal(readReplyWatermark("conv-a"), 12);
assert.equal(storage.getItem("panels.replySeen.conv-a"), "12");

// The watermark only ever moves forward: a lower line number is ignored.
writeReplyWatermark("conv-a", 4);
assert.equal(readReplyWatermark("conv-a"), 12);
writeReplyWatermark("conv-a", 12);
assert.equal(readReplyWatermark("conv-a"), 12);
writeReplyWatermark("conv-a", 30);
assert.equal(readReplyWatermark("conv-a"), 30);

// Conversations are independent — an old conversation's watermark cannot suppress a
// fresh one's replies, which is the whole reason the key is the conversation id.
assert.equal(readReplyWatermark("conv-b"), 0);
writeReplyWatermark("conv-b", 3);
assert.equal(readReplyWatermark("conv-b"), 3);
assert.equal(readReplyWatermark("conv-a"), 30);

// A stored value that is not a line number reads as unread rather than as a position.
for (const nonsense of ["", "  ", "seven", "3.5", "-2", "NaN"]) {
  storage.entries.set("panels.replySeen.conv-broken", nonsense);
  assert.equal(readReplyWatermark("conv-broken"), 0, nonsense);
}

// A line number that is not a whole position is not written.
storage.entries.delete("panels.replySeen.conv-fractional");
writeReplyWatermark("conv-fractional", 2.5);
writeReplyWatermark("conv-fractional", Number.NaN);
assert.equal(readReplyWatermark("conv-fractional"), 0);
assert.equal(storage.getItem("panels.replySeen.conv-fractional"), null);

// A store that refuses the write leaves the conversation unread; it does not throw.
storage.rejectWrites = true;
writeReplyWatermark("conv-full", 9);
assert.equal(readReplyWatermark("conv-full"), 0);
storage.rejectWrites = false;

// A store that throws on read leaves the conversation unread too.
globalThis.localStorage = {
  getItem() {
    throw new Error("storage is blocked");
  },
  setItem() {}
};
assert.equal(readReplyWatermark("conv-a"), 0);

// No storage at all (a non-browser environment): read is 0 and write is a no-op.
globalThis.localStorage = undefined;
assert.equal(readReplyWatermark("conv-a"), 0);
writeReplyWatermark("conv-a", 40);

globalThis.localStorage = storage;
assert.equal(readReplyWatermark("conv-a"), 30);

console.log("reply-watermark.test.mjs: all assertions passed");
