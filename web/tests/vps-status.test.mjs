import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const component = await readFile(new URL("../src/components/VpsStatusPopover.svelte", import.meta.url), "utf8");
const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const catalogue = await readFile(new URL("../src/lib/queryCatalogue.ts", import.meta.url), "utf8");

assert.match(component, /fetchJson<VpsStatusSnapshot>\("\/api\/vps-status"\)/);
assert.match(component, /onclick=\{toggle\}/);
assert.match(component, /Refresh/);
assert.match(component, /healthy.*warning.*critical.*unavailable.*review_needed/s);
assert.doesNotMatch(component, /onMount|setInterval|setTimeout|WebSocket|cleanup/);
assert.match(component, /connectionState.*ConnectionStatus/);
assert.match(component, /data-connection-status/);
assert.match(component, /connected: "Connected".*reconnecting: "Reconnecting"/s);
assert.match(component, /aria-label=\{`\$\{connectionLabels\[connectionState\]\}\. Show VPS status`\}/);
assert.match(app, /<VpsStatusPopover connectionState=\{\$connectionStatus\} \/>/);
assert.doesNotMatch(app, /class="shell-connection"|connectionLabels/);
assert.doesNotMatch(catalogue, /vps-status/);
assert.doesNotMatch(app, /#\/status|name === "status"/);
