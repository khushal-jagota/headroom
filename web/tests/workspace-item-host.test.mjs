import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const route = await readFile(
  new URL("../src/routes/BoardRoute.svelte", import.meta.url),
  "utf8",
);
const css = await readFile(new URL("../../assets/app.css", import.meta.url), "utf8");

// The app parser carries the Item identity into the one Workspace host.
assert.match(app, /if \(selection\.kind === "item"\) params\.item = selection\.id/);
assert.match(app, /<BoardRoute ticketId=\{route\.params\.id\} itemId=\{route\.params\.item\} \/>/);

// The host resolves the Item from the board, opens the existing Item workspace, and
// replaces a settled stale address only after a successful board read.
assert.match(route, /rail\.items\.find\(\(item\) => item\.id === itemId\)/);
assert.match(route, /<SprintItemWorkspace itemId=\{selectedItem\.id\}/);
assert.match(route, /const staleItem = itemId && !selectedItem/);
assert.match(
  route,
  /\(staleTicket \|\| staleItem\)[\s\S]*board\.data[\s\S]*!board\.isFetching[\s\S]*!board\.isError[\s\S]*window\.location\.replace\(workspaceAddress\(\{ kind: "none" \}\)\)/,
);

// Item selection keeps its Workspace address at every width. The narrow host hides
// the rail and shows the Item pane, unlike narrow Ticket selection.
assert.match(route, /function selectItem\(id: string\)[\s\S]*workspaceAddress\(\{ kind: "item", id \}\)/);
assert.doesNotMatch(
  route.slice(route.indexOf("function selectItem"), route.indexOf("function toggleRailMode")),
  /matchMedia|#\/ticket/,
);
assert.match(
  css,
  /@media \(max-width: 960px\)[\s\S]*\.board-workspace-shell--item \.board-workspace-left\s*\{[^}]*display: none[\s\S]*\.board-workspace-right\.board-workspace-right--item\s*\{[^}]*display: flex/,
);

console.log("workspace-item-host.test.mjs: all assertions passed");
