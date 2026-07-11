import assert from "node:assert/strict";

import {
  clearSentPendingChatImages,
  createPendingChatImages,
  pendingChatImageFiles,
  removePendingChatImage,
  revokePendingChatImages
} from "../src/lib/chatImages.js";

const imageA = { name: "a.png", type: "image/png" };
const imageB = { name: "b.webp", type: "image/webp" };
const text = { name: "note.txt", type: "text/plain" };
const created = [];
const revoked = [];

const first = createPendingChatImages([imageA, text, imageB], 7, (file) => {
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
assert.deepEqual(pendingChatImageFiles(first.accepted), [imageA, imageB]);

const afterRemove = removePendingChatImage(first.accepted, 7, (url) => revoked.push(url));
assert.deepEqual(afterRemove.map((image) => image.file.name), ["b.webp"]);
assert.deepEqual(revoked, ["blob:a.png"]);

const afterSend = clearSentPendingChatImages(first.accepted, [8], (url) => revoked.push(url));
assert.deepEqual(afterSend.map((image) => image.file.name), ["a.png"]);
assert.deepEqual(revoked, ["blob:a.png", "blob:b.webp"]);

revokePendingChatImages(afterSend, (url) => revoked.push(url));
assert.deepEqual(revoked, ["blob:a.png", "blob:b.webp", "blob:a.png"]);
