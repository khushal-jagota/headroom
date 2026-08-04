import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const route = await readFile(
  new URL("../src/routes/BoardRoute.svelte", import.meta.url),
  "utf8",
);
const chief = await readFile(
  new URL("../src/components/ChiefConversation.svelte", import.meta.url),
  "utf8",
);
const css = await readFile(new URL("../../assets/app.css", import.meta.url), "utf8");
const queries = await readFile(
  new URL("../src/lib/queryCatalogue.ts", import.meta.url),
  "utf8",
);
const chiefProfile = await readFile(
  new URL("../src/assets/chief-of-staff-profile.webp", import.meta.url),
);

// The direct shell controls have one stable order, and Home is not duplicated in More.
const directNavigation = [
  app.indexOf('data-screen="home"'),
  app.indexOf('data-screen="review"'),
  app.indexOf('data-screen="workspace"'),
  app.indexOf('data-screen="more"'),
];
assert.ok(directNavigation.every((position) => position >= 0));
assert.deepEqual([...directNavigation].sort((left, right) => left - right), directNavigation);
const moreMenu = app.slice(
  app.indexOf('data-shell-more-menu'),
  app.indexOf("</div>", app.indexOf('href="#/config"')) + 6,
);
assert.doesNotMatch(moreMenu, /href="#\/agents"/);
assert.match(moreMenu, /href="#\/config"/);

// Both runtime addresses mount one component; selection is a route input, not another
// conversation implementation.
assert.doesNotMatch(app, /AgentsRoute|data-screen="agents-nav"/);
assert.match(route, /ChiefConversation/);
assert.match(route, /data-chief-destination/);
assert.match(route, /conversationSignalPresentation/);
assert.match(route, /onReplyWatermarkMoved/);
assert.match(route, /<StageMark/);
assert.doesNotMatch(route, /agents-workspace|agents-roster/);

// The one agent identity in Workspace owns one compact, bundled portrait. It is
// decorative beside the visible name, and ticket rows retain their existing shape.
assert.match(
  route,
  /import chiefOfStaffProfile from "\.\.\/assets\/chief-of-staff-profile\.webp"/,
);
assert.equal(route.match(/class="board-workspace-agent-profile"/g)?.length, 1);
assert.match(route, /src=\{chiefOfStaffProfile\}/);
assert.match(route, /alt=""/);
assert.match(route, /aria-hidden="true"/);
assert.equal(chiefProfile.subarray(0, 4).toString("ascii"), "RIFF");
assert.equal(chiefProfile.subarray(8, 12).toString("ascii"), "WEBP");
assert.ok(chiefProfile.byteLength < 64 * 1024);
assert.match(
  css,
  /\.board-workspace-agent-profile\s*\{[^}]*width: var\(--space-6\)[^}]*height: var\(--space-6\)[^}]*object-fit: cover[^}]*\}/s,
);

// The Chief wrapper owns the canonical owner API and exposes lookup failure recovery.
for (const endpoint of [
  "/api/chief/conversation",
  "/api/chief/conversation/start-values",
  "/api/chief/conversation/send",
  "/api/chief/conversation/reset",
]) {
  assert.ok(
    chief.includes(endpoint) || queries.includes(endpoint),
    `missing canonical Chief endpoint ${endpoint}`,
  );
}
assert.match(chief, /queries\.chiefConversation\(\)/);
assert.match(chief, /data-chief-conversation-loading/);
assert.match(chief, /data-chief-conversation-error/);
assert.match(chief, /data-chief-conversation-retry/);
assert.match(chief, /currentConversation\.refetch\(\)/);
