import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  clearSentPendingConversationImages,
  createPendingConversationImages,
  pendingConversationImageFiles,
  removePendingConversationImage,
  revokePendingConversationImages
} from "../src/lib/acp/pendingConversationImages.js";

const imageA = { name: "a.png", type: "image/png" };
const imageB = { name: "b.webp", type: "image/webp" };
const text = { name: "note.txt", type: "text/plain" };
const created = [];
const revoked = [];

const first = createPendingConversationImages([imageA, text, imageB], 7, (file) => {
  const url = `blob:${file.name}`;
  created.push(url);
  return url;
});

assert.deepEqual(
  first.accepted.map((image) => [image.id, image.file.name, image.url]),
  [
    [7, "a.png", "blob:a.png"],
    [8, "b.webp", "blob:b.webp"]
  ]
);
assert.deepEqual(first.rejected.map((file) => file.name), ["note.txt"]);
assert.equal(first.nextId, 9);
assert.deepEqual(created, ["blob:a.png", "blob:b.webp"]);
assert.deepEqual(pendingConversationImageFiles(first.accepted), [imageA, imageB]);

const afterRemove = removePendingConversationImage(first.accepted, 7, (url) => revoked.push(url));
assert.deepEqual(afterRemove.map((image) => image.file.name), ["b.webp"]);
assert.deepEqual(revoked, ["blob:a.png"]);

const afterSend = clearSentPendingConversationImages(first.accepted, [8], (url) => revoked.push(url));
assert.deepEqual(afterSend.map((image) => image.file.name), ["a.png"]);
assert.deepEqual(revoked, ["blob:a.png", "blob:b.webp"]);

revokePendingConversationImages(afterSend, (url) => revoked.push(url));
assert.deepEqual(revoked, ["blob:a.png", "blob:b.webp", "blob:a.png"]);

const composerSource = await readFile(
  new URL("../src/components/acp/ConversationComposer.svelte", import.meta.url),
  "utf8"
);
const acpComposerSource = await readFile(
  new URL("../src/components/acp/AcpComposer.svelte", import.meta.url),
  "utf8"
);
assert.match(composerSource, /pendingConversationImageFiles/);
assert.match(acpComposerSource, /type:\s*["']image["']/);
assert.match(acpComposerSource, /data:\s*btoa\(binary\)/);
assert.doesNotMatch(`${composerSource}\n${acpComposerSource}`, /\/api\/chat|\/files\/chats|uploadChatImage|fetch\s*\(/);

console.log("acp-images.test.mjs: all assertions passed");
