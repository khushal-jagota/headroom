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
// replaces a settled stale address only after a successful board read. A Ticket is not
// resolved from the board, because a Ticket opens here from anywhere.
assert.match(route, /rail\.items\.find\(\(item\) => item\.id === itemId\)/);
assert.match(route, /<SprintItemWorkspace itemId=\{selectedItem\.id\}/);
assert.match(route, /const staleItem = itemId && !selectedItem/);
assert.doesNotMatch(route, /staleTicket/);
assert.match(
  route,
  /staleItem && board\.data && !board\.isFetching && !board\.isError[\s\S]*window\.location\.replace\(workspaceAddress\(\{ kind: "none" \}\)\)/,
);
assert.match(route, /<TicketRoute id=\{ticketId\} \/>/);

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

// A Ticket opened from inside an Item takes the pane and leaves the Item open, so what
// the rail holds open is its own state and only falls back to the address. An Item in
// the address is recorded as the open one, so a Ticket opened after a reload still finds
// its Item open. Held open is not selected, and nothing in the rail shuts an Item.
assert.match(route, /let railItemId = \$derived\(openedItemId \?\? itemId \?\? null\)/);
assert.match(route, /\$effect\(\(\) => \{\s*if \(itemId\) \{\s*openedItemId = itemId;/);
assert.match(route, /\{@const open = railItemId === item\.id\}/);
assert.match(route, /function selectItem\(id: string\)[\s\S]*openedItemId = id;/);
assert.match(
  css,
  /@media \(max-width: 960px\)[\s\S]*\.board-workspace-shell--item \.board-workspace-left\s*\{[^}]*display: none[\s\S]*\.board-workspace-right\.board-workspace-right--item\s*\{[^}]*display: flex/,
);

console.log("workspace-item-host.test.mjs: all assertions passed");
