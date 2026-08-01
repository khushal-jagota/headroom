import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const route = await readFile(
  new URL("../src/routes/AgentsRoute.svelte", import.meta.url),
  "utf8",
);
const chief = await readFile(
  new URL("../src/components/ChiefConversation.svelte", import.meta.url),
  "utf8",
);
const queries = await readFile(
  new URL("../src/lib/queryCatalogue.ts", import.meta.url),
  "utf8",
);

// The direct shell controls have one stable order, and Agents is not duplicated in More.
const directNavigation = [
  app.indexOf('data-screen="review"'),
  app.indexOf('data-screen="workspace"'),
  app.indexOf('data-screen="agents-nav"'),
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
assert.match(app, /selectedAgent=\{route\.params\.roleKind === "chief"/);
assert.match(app, /name === "agents"\s*\?\s*"agents"/);
assert.match(route, /ChiefConversation/);
assert.doesNotMatch(route, /fetchJson|mutateJson|\/api\/chief\/conversation/);
assert.match(route, /conversationSignalPresentation/);
assert.match(route, /onReplyWatermarkMoved/);
assert.match(route, /<StageMark/);
assert.doesNotMatch(route, /agents-workspace-roster-head/);
assert.doesNotMatch(route, /agents-roster-description|agents-roster-arrow/);

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
