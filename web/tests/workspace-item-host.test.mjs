import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const route = await readFile(
  new URL("../src/routes/BoardRoute.svelte", import.meta.url),
  "utf8",
);
const itemWorkspace = await readFile(
  new URL("../src/components/SprintItemWorkspace.svelte", import.meta.url),
  "utf8",
);
const css = await readFile(new URL("../../assets/app.css", import.meta.url), "utf8");

// The app parser hands the whole parsed address to the one Workspace host, and the
// screen key never carries it, so moving around the Workspace does not remount it.
assert.match(app, /return workspaceRoute\(address\)/);
assert.match(
  app,
  /function workspaceRoute\(address: WorkspaceAddress\): Route \{\s*return \{ name: "workspace", params: \{\}, key: "workspace", workspace: address \};/,
);
assert.match(app, /<BoardRoute address=\{route\.workspace\} \/>/);

// The host resolves the open Item from the board, opens the existing Item workspace, and
// replaces a settled stale address only after a successful board read. A Ticket is not
// resolved from the board, because a Ticket opens here from anywhere.
assert.match(route, /rail\.items\.find\(\(item\) => item\.id === opening\.openItemId\)/);
assert.match(route, /<SprintItemWorkspace itemId=\{openItem\.id\}/);
assert.match(route, /const staleItem = opening\.openItemId && !openItem/);
assert.doesNotMatch(route, /staleTicket/);
assert.match(
  route,
  /staleItem && board\.data && !board\.isFetching && !board\.isError[\s\S]*window\.location\.replace\(kept\)/,
);
// A stale Item does not take the Ticket in the pane down with it.
assert.match(
  route,
  /const kept = opening\.markedTicketId\s*\? workspaceAddress\(\{ kind: "ticket", id: opening\.markedTicketId \}\)\s*: workspaceAddress\(\{ kind: "none" \}\)/,
);
assert.match(route, /<TicketRoute id=\{opening\.markedTicketId\} \/>/);

// Item selection keeps its Workspace address at every width, and every click on an Item
// opens it. The narrow host hides the rail and shows the Item pane, and narrow Ticket
// selection now does the same.
assert.match(
  route,
  /function selectItem\(id: string\)[\s\S]*window\.location\.hash = workspaceAddress\(\{ kind: "item", id \}\)/,
);
assert.doesNotMatch(
  route.slice(
    route.indexOf("function selectItem"),
    route.indexOf("let howFarThisBrowserHasRead"),
  ),
  /matchMedia|#\/ticket|shutting|kind: "none"/,
);

// What is open is a function of the address and nothing else. The screen keeps no state
// that can hold a row open, or hold a view, once the address moves.
assert.doesNotMatch(route, /openedItemId|railItemId|chosenView/);
assert.match(route, /let opening = \$derived\(whatTheAddressOpens\(address\)\)/);
assert.match(route, /\{@const open = opening\.openItemId === item\.id\}/);
// A Ticket opened from inside an Item names that Item in the address, and a Ticket in
// the Sprint Item pane does the same, so the pane cannot close the Item showing it.
assert.match(
  route,
  /function selectCard\(id: string, insideItemId: string \| null\)[\s\S]*openedFromItemId: insideItemId/,
);
assert.match(route, /\{@render ticketGroups\(item\.groups, false, item\.id\)\}/);
assert.match(
  itemWorkspace,
  /workspaceAddress\(\{ kind: "ticket", id: ticket\.id, openedFromItemId: itemId \}\)/,
);
// Changing the list the rail shows is an address too, and it keeps the pane.
assert.match(
  route,
  /function showView\([\s\S]*window\.location\.hash = workspaceAddress\(address\.selection, next\)/,
);

assert.match(
  css,
  /@media \(max-width: 960px\)[\s\S]*\.board-workspace-shell--item \.board-workspace-left\s*\{[^}]*display: none[\s\S]*\.board-workspace-right\.board-workspace-right--item\s*\{[^}]*display: flex/,
);

console.log("workspace-item-host.test.mjs: all assertions passed");
