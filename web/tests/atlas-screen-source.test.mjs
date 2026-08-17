import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const route = await readFile(new URL("../src/routes/AtlasRoute.svelte", import.meta.url), "utf8");
const panel = await readFile(
  new URL("../src/components/atlas/AtlasPanel.svelte", import.meta.url),
  "utf8",
);
const css = await readFile(new URL("../../assets/app.css", import.meta.url), "utf8");

// Atlas is a screen of the app: one address, one navigation entry, one mount.
assert.match(app, /import AtlasRoute from ".\/routes\/AtlasRoute.svelte";/);
assert.match(app, /href="#\/atlas"/);
assert.match(app, /route.name === "atlas"/);
assert.match(app, /<AtlasRoute \/>/);

// It is reachable, not just routable: the address is in the known-route allowlist,
// and the More trigger lights while it is open.
const knownRoutes = app.slice(app.indexOf("function isKnownRoute"), app.indexOf("function secondaryRouteActive"));
assert.match(knownRoutes, /"atlas"/);
assert.match(app.slice(app.indexOf("function secondaryRouteActive")), /"atlas"/);

// The world re-presents the board every other screen reads. It adds no read of its
// own and computes no new signal, so it goes through the named-read catalogue.
assert.match(route, /queries\.board\(\)/);
assert.match(route, /queries\.review\(\)/);
assert.doesNotMatch(route, /fetch\(/);
assert.doesNotMatch(route, /EventSource/);

// three.js belongs to this screen alone. A static import would put it in the bundle
// every other screen loads.
assert.match(route, /await import\("\.\.\/lib\/atlas\/world"\)|import\("\.\.\/lib\/atlas\/world"\)/);
assert.doesNotMatch(route, /^import .* from "three"/m);

// The panel raises the real screens. Not copies of them, and not new ones.
assert.match(route, /<TicketRoute id=/);
assert.match(route, /<SprintItemWorkspace/);
assert.match(route, /import TicketRoute from ".\/TicketRoute.svelte";/);
assert.match(route, /import SprintItemWorkspace from "..\/components\/SprintItemWorkspace.svelte";/);

// The world is disposed with the screen. A screen that leaks a renderer costs the
// browser a context every time somebody looks at it.
assert.match(route, /\.dispose\(\)/);

// Escape belongs to the conversation first; the panel only takes it when the
// conversation is resting.
assert.match(panel, /data-conversation-pane/);
assert.match(panel, /data-conversation-state/);

// The panel's width comes from the measure the real screens read at, never from
// whatever space happens to be available.
const panelRule = css.slice(css.indexOf(".atlas-panel {"), css.indexOf(".atlas-panel-body"));
assert.match(panelRule, /var\(--ticket-column-measure\)/);
assert.doesNotMatch(panelRule, /width: 640px/);

// A link inside the panel moves Atlas. The route owns where Atlas is, so the panel
// reports the place and does not travel by itself.
assert.match(route, /onNavigate=\{navigateTo\}/);
assert.match(route, /scene\?\.select\(next, \{ travel: true \}\)/);
assert.doesNotMatch(panel, /scene/);

// The back line is the chain of links the reader followed, and it is dropped by
// everything that returns them to the world.
const closePanel = route.slice(route.indexOf("function closePanel"), route.indexOf("function goHome"));
assert.match(closePanel, /backStack = \[\]/);
assert.match(route, /function goHome\(\): void \{\s*closePanel\(\);/);
assert.match(route, /onBack=\{backStack\.length > 0 \? goBack : null\}/);

// On a phone it is a bottom sheet, and the world keeps the top of the screen.
const narrow = css.slice(css.indexOf("@media (max-width: 720px) {", css.indexOf(".atlas-panel {")));
assert.match(narrow, /\.atlas-panel {/);

console.log("atlas screen source contract ok");
