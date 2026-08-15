import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const route = await readFile(
  new URL("../src/routes/BoardRoute.svelte", import.meta.url),
  "utf8",
);
const css = await readFile(new URL("../../assets/app.css", import.meta.url), "utf8");

// Awake beats rested: needs-me, a running turn, or an unseen reply keeps an Item live
// even when every one of its Tickets is done.
assert.match(
  route,
  /function itemIsAwake\(item: WorkspaceRailItem\): boolean \{[\s\S]*state === "needs-me" \|\| state === "current-running" \|\| state === "current-awaiting-approval"/,
);

// The partition is stable and separate from rail.items's own priority-then-age sort:
// live-or-awake Items keep their order, then rested-and-quiet Items follow in theirs.
assert.match(
  route,
  /function partitionByRest\(items: readonly WorkspaceRailItem\[\]\): WorkspaceRailItem\[\] \{\s*const live = items\.filter\(\(item\) => !item\.rested \|\| itemIsAwake\(item\)\);\s*const rested = items\.filter\(\(item\) => item\.rested && !itemIsAwake\(item\)\);\s*return \[\.\.\.live, \.\.\.rested\];/,
);
assert.match(route, /let orderedItems = \$derived\(partitionByRest\(rail\.items\)\)/);
assert.match(route, /\{#each orderedItems as item \(item\.id\)\}/);
assert.doesNotMatch(route, /\{#each rail\.items as item \(item\.id\)\}/);

// A rested-and-quiet Item's title gets the dim modifier class; nothing else about the
// row's markup changes.
assert.match(
  route,
  /class:board-workspace-item-title--rested=\{item\.rested && !itemIsAwake\(item\)\}/,
);

// The modifier only touches color and weight; the box, hairline, padding, and gap
// live on the unmodified `.board-workspace-item` rules.
assert.match(
  css,
  /\.board-workspace-item-title--rested\s*\{\s*color: var\(--text-faint\);\s*font-weight: 400;\s*\}/,
);

console.log("workspace-item-rest.test.mjs: all assertions passed");
