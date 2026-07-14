import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import ts from "typescript";

const srcRoot = new URL("../src/", import.meta.url);
const ownerUrl = new URL("../src/lib/resourceCatalogue.ts", import.meta.url);
const ownerSource = await readFile(ownerUrl, "utf8");

async function productionFiles(directory, suffixes) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) files.push(...(await productionFiles(url, suffixes)));
    else if (suffixes.some((suffix) => entry.name.endsWith(suffix))) files.push(url);
  }
  return files;
}

const expectedMethods = [
  "board",
  "review",
  "todayDay",
  "backlogSprintItems",
  "ideas",
  "projects",
  "sprintSummaries",
  "currentSprint",
  "ticket",
  "panelsChat",
  "chatGatewayStatus",
  "chatCommands",
  "workerTypeManifests"
];
const expectedPublicDeclarations = [
  "export type EventEntityPrefix",
  "export type CatalogueResourceIdentity",
  "export type PlannerEvent",
  "export type ResourceEventContext",
  "export type ResourceMutationEffect",
  "export interface ResourceCatalogue",
  "export const resourceCatalogue",
  "export function mutateJsonWithResourceEffect",
  "export function keysForEvent",
  "export function invalidateCatalogueResources",
  "export function knownEntityPrefixes",
  'export type { ResourceHandle } from "./resources.svelte"'
];
for (const declaration of expectedPublicDeclarations) {
  assert.ok(ownerSource.includes(declaration), declaration);
}
assert.match(ownerSource, /const RESOURCE_DEFINITIONS\s*=/);
assert.match(ownerSource, /const OPENED_PARAMETERIZED_IDENTITIES\s*=\s*new Set/);
assert.doesNotMatch(ownerSource, /\?\?\s*["']coding["']|\|\|\s*["']coding["']/);

const productionUrls = await productionFiles(srcRoot, [".ts", ".svelte"]);
const production = new Map(
  await Promise.all(
    productionUrls.map(async (url) => [url.pathname, await readFile(url, "utf8")])
  )
);
for (const [path, source] of production) {
  const name = basename(path);
  if (name !== "resourceCatalogue.ts" && name !== "resources.svelte.ts") {
    assert.doesNotMatch(source, /\bresource\s*\(/, `${name} opens the generic cache`);
    assert.doesNotMatch(source, /from\s+["'][^"']*resources(?:\.svelte)?["']/, `${name} imports the cache engine`);
  }
  if (name !== "resourceCatalogue.ts" && name !== "resources.svelte.ts") {
    assert.doesNotMatch(source, /\b(?:invalidateMany|peek)\s*\(/, `${name} bypasses the catalogue`);
  }
  assert.doesNotMatch(source, /manifestResource/, `${name} keeps the deleted manifest forwarder`);
  assert.doesNotMatch(source, /expectedInvalidations/, `${name} keeps free-form mutation effects`);
}
for (const deleted of [
  "eventMapping.mjs",
  "eventMapping.d.ts",
  "resources.ts",
  "manifest.svelte.ts"
]) {
  assert.ok(![...production.keys()].some((path) => path.endsWith(`/${deleted}`)), deleted);
}
const cacheSource = await readFile(new URL("../src/lib/resources.svelte.ts", import.meta.url), "utf8");
assert.doesNotMatch(cacheSource, /mutateJson|\n\s+invalidate\s*\([^)]*\)\s*\{/);
assert.doesNotMatch(cacheSource, /Project|Ticket|PlannerEvent|ResourceMutationEffect/);

const opened = [];
const fetches = [];
const invalidations = [];
const refreshes = [];
const dataByIdentity = new Map();
const staleByIdentity = new Map();
let mutationResult = { ok: true };
let mutationError = null;
let nextReviewRefresh = null;

function fakeHandle(key, fetcher) {
  return {
    key,
    fetcher,
    get data() {
      return dataByIdentity.get(key);
    },
    get error() {
      return null;
    },
    get loading() {
      return false;
    },
    get stale() {
      return staleByIdentity.get(key) ?? true;
    },
    refresh() {
      return Promise.resolve(dataByIdentity.get(key));
    },
    dispose() {}
  };
}

globalThis.__catalogueApi = {
  fetchJson: async (path, options = {}) => {
    fetches.push({ path, options });
    if (mutationError) throw mutationError;
    return mutationResult;
  }
};
globalThis.__catalogueCache = {
  resource: (key, fetcher) => {
    const handle = fakeHandle(key, fetcher);
    opened.push(handle);
    return handle;
  },
  peek: (key) => dataByIdentity.get(key),
  invalidateMany: (identities, reason) => invalidations.push({ identities: [...identities], reason }),
  refresh: (key) => {
    refreshes.push(key);
    return nextReviewRefresh || Promise.resolve(dataByIdentity.get(key));
  }
};

const executableSource = ownerSource
  .replace(
    'import { fetchJson, type FetchOptions } from "./api";',
    "const { fetchJson } = globalThis.__catalogueApi;"
  )
  .replace(
    /import\s*\{\s*invalidateMany,\s*peek,\s*refresh,\s*resource,\s*type ResourceHandle\s*\}\s*from\s*["']\.\/resources\.svelte["'];/,
    "const { invalidateMany, peek, refresh, resource } = globalThis.__catalogueCache;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const tempDir = await mkdtemp(join(tmpdir(), "planner-resource-catalogue-"));
const modulePath = join(tempDir, "resourceCatalogue.mjs");
await writeFile(modulePath, compiled, "utf8");
const catalogueModule = await import(modulePath);
await rm(tempDir, { recursive: true, force: true });
const {
  resourceCatalogue,
  mutateJsonWithResourceEffect,
  keysForEvent,
  invalidateCatalogueResources,
  knownEntityPrefixes
} = catalogueModule;

assert.deepEqual(Object.keys(resourceCatalogue), expectedMethods);

const opens = [
  [() => resourceCatalogue.board(), "board", "/api/board"],
  [() => resourceCatalogue.review(), "review", "/api/review"],
  [() => resourceCatalogue.todayDay(), "day:today", "/api/day/today"],
  [() => resourceCatalogue.backlogSprintItems(), "items:backlog", "/api/items?sprint_id=null"],
  [() => resourceCatalogue.ideas(), "ideas", "/api/ideas"],
  [() => resourceCatalogue.projects(), "projects", "/api/projects"],
  [() => resourceCatalogue.sprintSummaries(), "sprints", "/api/sprints"],
  [() => resourceCatalogue.currentSprint(), "sprint:current", "/api/sprint/current"],
  [() => resourceCatalogue.ticket("t_a/b"), "ticket:t_a/b", "/api/tickets/t_a%2Fb"],
  [() => resourceCatalogue.panelsChat("t_a/b"), "chat:t_a/b", "/api/chat/t_a%2Fb/state"],
  [() => resourceCatalogue.chatGatewayStatus("agent_a/b"), "chat-status:agent_a/b", "/api/chat/agent_a%2Fb/status"],
  [() => resourceCatalogue.chatCommands(), "chat-commands", "/api/chat/commands"],
  [() => resourceCatalogue.workerTypeManifests(), "worker-types", "/api/worker-types"]
];
for (const [open, identity, path] of opens) {
  const handle = open();
  assert.equal(handle.key, identity);
  await handle.fetcher(new AbortController().signal);
  assert.equal(fetches.at(-1).path, path);
  assert.ok(fetches.at(-1).options.signal instanceof AbortSignal);
}
assert.throws(() => resourceCatalogue.ticket(""), /non-empty string/);
assert.throws(() => resourceCatalogue.panelsChat(null), /non-empty string/);
assert.equal(resourceCatalogue.ticket(" t_exact ").key, "ticket: t_exact ");
assert.equal(opened.at(-1).key, "ticket: t_exact ");
await opened.at(-1).fetcher(new AbortController().signal);
assert.equal(fetches.at(-1).path, "/api/tickets/%20t_exact%20");
dataByIdentity.set("ticket:t_a/b", { id: "t_a/b" });
dataByIdentity.set("ticket: t_exact ", { id: " t_exact " });

dataByIdentity.set("chat:t_cached", { messages: [] });
staleByIdentity.set("chat:t_cached", false);
const cachedChatRefreshCount = refreshes.length;
resourceCatalogue.panelsChat("t_cached");
assert.deepEqual(refreshes.slice(cachedChatRefreshCount), ["chat:t_cached"]);
dataByIdentity.delete("chat:t_new");
staleByIdentity.set("chat:t_new", true);
const newChatRefreshCount = refreshes.length;
resourceCatalogue.panelsChat("t_new");
assert.equal(refreshes.length, newChatRefreshCount);

const effectCases = [
  [{ kind: "ideaCreated" }, ["ideas"], []],
  [{ kind: "backlogSprintItemCreated" }, ["items:backlog", "sprint:current", "board"], []],
  [{ kind: "ticketChanged", ticketId: "t_effect" }, ["ticket:t_effect", "board", "sprint:current"], []],
  [{ kind: "ticketTitleChanged", ticketId: "t_effect" }, ["ticket:t_effect", "board", "sprint:current", "review"], []],
  [{ kind: "ticketReviewStateChanged", ticketId: "t_effect" }, ["ticket:t_effect", "board", "sprint:current", "review"], []],
  [{ kind: "reviewTicketAccepted", ticketId: "t_effect" }, ["ticket:t_effect", "board", "sprint:current"], ["review"]],
  [{ kind: "reviewTicketReturnedForRevision", ticketId: "t_effect" }, ["ticket:t_effect", "chat:t_effect", "board", "sprint:current"], ["review"]],
  [{ kind: "todayDayChanged" }, ["day:today"], []],
  [{ kind: "currentSprintChanged" }, ["sprint:current", "sprints"], []]
];
for (const [effect, identities, orderedRefreshes] of effectCases) {
  invalidations.length = 0;
  refreshes.length = 0;
  mutationResult = { effect: effect.kind };
  const result = await mutateJsonWithResourceEffect("/mutation", { method: "POST" }, effect);
  assert.deepEqual(result, mutationResult);
  assert.deepEqual(invalidations, [{ identities, reason: "successful mutation" }]);
  assert.deepEqual(refreshes, orderedRefreshes);
}

invalidations.length = 0;
refreshes.length = 0;
const failedMutation = new Error("write failed");
mutationError = failedMutation;
await assert.rejects(
  mutateJsonWithResourceEffect("/mutation", { method: "POST" }, { kind: "ideaCreated" }),
  (error) => error === failedMutation
);
assert.deepEqual(invalidations, []);
assert.deepEqual(refreshes, []);
mutationError = null;

let releaseReviewRefresh;
nextReviewRefresh = new Promise((resolvePromise) => {
  releaseReviewRefresh = resolvePromise;
});
let orderedResolved = false;
const orderedMutation = mutateJsonWithResourceEffect(
  "/mutation",
  { method: "POST" },
  { kind: "reviewTicketAccepted", ticketId: "t_ordered" }
).then(() => {
  orderedResolved = true;
});
await Promise.resolve();
assert.equal(orderedResolved, false);
assert.deepEqual(refreshes.slice(-1), ["review"]);
releaseReviewRefresh();
await orderedMutation;
nextReviewRefresh = Promise.reject(new Error("review failed"));
await assert.doesNotReject(
  mutateJsonWithResourceEffect(
    "/mutation",
    { method: "POST" },
    { kind: "reviewTicketReturnedForRevision", ticketId: "t_refresh_error" }
  )
);
nextReviewRefresh = null;

function event(entity_id, kind = "synthetic_new_kind", payload = {}) {
  return { id: 1, entity_id, kind, payload, created_at: 1 };
}
function sorted(values) {
  return [...values].sort();
}
function exact(actual, expected) {
  assert.deepEqual(sorted(actual), sorted(expected));
}

assert.deepEqual(knownEntityPrefixes(), ["t", "si", "sp", "day", "idea", "project", "agent"]);
exact(keysForEvent(event("t_demo")), ["ticket:t_demo", "board", "sprint:current"]);
exact(keysForEvent(event("si_demo")), ["items:backlog", "board", "sprint:current"]);
exact(keysForEvent(event("sp_demo")), ["sprints", "sprint:current"]);
exact(keysForEvent(event("day_today"), { todayDayId: "day_today" }), ["day:today"]);
exact(keysForEvent(event("day_other"), { todayDayId: "day_today" }), []);
exact(keysForEvent(event("day_cold"), { todayDayId: null }), ["day:today"]);
exact(keysForEvent(event("idea_demo")), ["ideas"]);
exact(keysForEvent(event("project_demo")), ["projects"]);
exact(keysForEvent(event("agent_demo")), ["chat:agent_demo"]);
assert.throws(() => keysForEvent(event("bad")), /unknown entity_id prefix/);
assert.throws(() => keysForEvent(event("x_demo")), /unknown entity_id prefix/);

for (const kind of [
  "chat_message_recorded",
  "chat_turn_started",
  "chat_turn_updated",
  "chat_turn_finished"
]) {
  exact(keysForEvent(event("t_chat", kind)), ["chat:t_chat"]);
}
exact(keysForEvent(event("t_chat", "chat_session_created")), ["ticket:t_chat", "chat:t_chat"]);
for (const kind of [
  "chat_session_created",
  "chat_message_recorded",
  "chat_turn_started",
  "chat_turn_updated",
  "chat_turn_finished"
]) {
  exact(keysForEvent(event("agent_chat", kind)), ["chat:agent_chat"]);
}

const reviewKinds = [
  "stage_changed",
  "proposal_accepted",
  "proposal_superseded",
  "proposal_filed",
  "kickoff_proposal_filed",
  "kickoff_accepted",
  "approval_returned",
  "ticket_status_changed",
  "ticket_deleted"
];
for (const kind of reviewKinds) assert.ok(keysForEvent(event("t_review", kind)).includes("review"));
assert.ok(keysForEvent(event("t_review", "ticket_updated", { field: "title" })).includes("review"));
for (const [kind, payload] of [
  ["ticket_created", {}],
  ["ticket_updated", { field: "priority" }],
  ["note_updated", {}],
  ["link_added", { from_id: "t_review", to_id: "si_other" }]
]) {
  assert.ok(!keysForEvent(event("t_review", kind, payload)).includes("review"));
}

exact(
  keysForEvent(event("t_left", "link_added", { from_id: "t_left", to_id: "si_right" })),
  ["ticket:t_left", "items:backlog", "board", "sprint:current"]
);
exact(
  keysForEvent(event("t_source", "stage_changed", {
    affected_blocked_target_ids: ["t_blocked", "si_blocked"]
  })),
  [
    "ticket:t_source",
    "ticket:t_blocked",
    "items:backlog",
    "board",
    "sprint:current",
    "review"
  ]
);
assert.throws(
  () => keysForEvent(event("t_left", "link_added", { from_id: "x_bad", to_id: "t_right" })),
  /unknown entity_id prefix/
);
assert.throws(
  () => keysForEvent(event("t_source", "stage_changed", { affected_blocked_target_ids: ["x_bad"] })),
  /unknown entity_id prefix/
);

for (const kind of ["day_ticket_added", "day_ticket_removed"]) {
  exact(keysForEvent(event("day_today", kind, { ticket_id: "t_day" }), { todayDayId: "day_today" }), [
    "day:today",
    "review",
    "ticket:t_day",
    "board"
  ]);
  exact(keysForEvent(event("day_other", kind, { ticket_id: "t_day" }), { todayDayId: "day_today" }), [
    "ticket:t_day",
    "board"
  ]);
  exact(keysForEvent(event("day_cold", kind, { ticket_id: "t_day" }), { todayDayId: null }), [
    "day:today",
    "review",
    "ticket:t_day",
    "board"
  ]);
}

exact(keysForEvent(event("project_alpha", "project_created")), ["projects"]);
exact(keysForEvent(event("project_alpha", "project_updated", { fields: ["summary"] })), ["projects"]);
exact(keysForEvent(event("project_alpha", "future_project_kind")), ["projects"]);
const projectNameBase = [
  "projects",
  "board",
  "day:today",
  "items:backlog",
  "ideas",
  "sprint:current"
];
exact(
  keysForEvent(event("project_alpha", "project_updated", { fields: ["name"] })),
  projectNameBase
);
exact(
  keysForEvent(event("project_alpha", "project_updated", { fields: ["summary", "name"] })),
  projectNameBase
);

resourceCatalogue.ticket("t_project_match");
resourceCatalogue.ticket("t_project_other");
resourceCatalogue.ticket("t_project_null");
resourceCatalogue.ticket("t_project_absent");
const unresolved = resourceCatalogue.ticket("t_project_unresolved");
unresolved.dispose();
dataByIdentity.set("ticket:t_project_match", { project_id: "project_alpha" });
dataByIdentity.set("ticket:t_project_other", { project_id: "project_beta" });
dataByIdentity.set("ticket:t_project_null", { project_id: null });
dataByIdentity.set("ticket:t_project_absent", { id: "t_project_absent" });
invalidateCatalogueResources(["ticket:t_raw_only"], "test raw identity");
const projectNameKeys = keysForEvent(
  event("project_alpha", "project_updated", { fields: ["name"] })
);
exact(projectNameKeys, [...projectNameBase, "ticket:t_project_match", "ticket:t_project_unresolved"]);
assert.ok(!projectNameKeys.includes("ticket:t_raw_only"));
assert.ok(!projectNameKeys.some((key) => key === "review" || key.startsWith("chat:")));
exact(keysForEvent(event("project_alpha", "project_updated", { fields: ["summary"] })), ["projects"]);

for (const identity of projectNameKeys) {
  assert.doesNotThrow(() => invalidateCatalogueResources([identity], "valid identity"));
}
assert.throws(
  () => invalidateCatalogueResources(["not-a-resource"], "invalid identity"),
  /unknown catalogue resource identity/
);
for (const keys of [
  keysForEvent(event("t_demo")),
  keysForEvent(event("si_demo")),
  keysForEvent(event("sp_demo")),
  keysForEvent(event("day_today"), { todayDayId: "day_today" }),
  keysForEvent(event("idea_demo")),
  keysForEvent(event("project_demo")),
  keysForEvent(event("agent_demo"))
]) {
  assert.ok(!keys.some((key) => key === "queues" || key.startsWith("item:") || key.startsWith("sprint:") && key !== "sprint:current" || /^day:(?!today$)/.test(key)));
}

const backendKinds = JSON.parse(process.env.PLANNER_EVENT_KINDS || "[]");
for (const kind of backendKinds) {
  let sample = event("t_backend", kind);
  if (kind.startsWith("day_")) {
    sample = event("day_today", kind, kind === "day_ticket_added" || kind === "day_ticket_removed" ? { ticket_id: "t_backend" } : {});
  } else if (["sprint_created", "sprint_updated"].includes(kind)) {
    sample = event("sp_backend", kind);
  } else if (["sprint_item_created", "item_updated", "item_children_changed"].includes(kind)) {
    sample = event("si_backend", kind);
  } else if (kind === "idea_created") {
    sample = event("idea_backend", kind);
  } else if (["project_created", "project_updated"].includes(kind)) {
    sample = event("project_backend", kind);
  } else if (["link_added", "link_removed"].includes(kind)) {
    sample = event("t_backend", kind, { from_id: "t_backend", to_id: "si_backend" });
  }
  assert.ok(keysForEvent(sample, { todayDayId: "day_today" }).length > 0, kind);
}

const wsSource = await readFile(new URL("../src/lib/ws.ts", import.meta.url), "utf8");
assert.equal((wsSource.match(/keysForEvent\(plannerEvent\)/g) || []).length, 1);
assert.doesNotMatch(wsSource, /\bpeek\s*\(|invalidateMany|includeTodayAlias|todayId\s*\(/);

// The effect union rejects unknown names and missing Ticket ids at compile time.
const configPath = resolve(new URL("../tsconfig.json", import.meta.url).pathname);
const configFile = ts.readConfigFile(configPath, ts.sys.readFile);
assert.equal(configFile.error, undefined);
const parsedConfig = ts.parseJsonConfigFileContent(configFile.config, ts.sys, resolve(configPath, ".."));
const typeDir = await mkdtemp(join(tmpdir(), "planner-resource-types-"));
const fixturePath = join(typeDir, "effects.ts");
const cataloguePath = resolve(ownerUrl.pathname).replace(/\\/g, "/");
await writeFile(
  fixturePath,
  `import type { ResourceMutationEffect } from ${JSON.stringify(cataloguePath)};\n` +
    `const all: ResourceMutationEffect[] = [\n` +
    `  { kind: "ideaCreated" }, { kind: "backlogSprintItemCreated" },\n` +
    `  { kind: "ticketChanged", ticketId: "t" },\n` +
    `  { kind: "ticketTitleChanged", ticketId: "t" },\n` +
    `  { kind: "ticketReviewStateChanged", ticketId: "t" },\n` +
    `  { kind: "reviewTicketAccepted", ticketId: "t" },\n` +
    `  { kind: "reviewTicketReturnedForRevision", ticketId: "t" },\n` +
    `  { kind: "todayDayChanged" }, { kind: "currentSprintChanged" }\n` +
    `];\nvoid all;\n` +
    `// @ts-expect-error unknown effect\nconst unknown: ResourceMutationEffect = { kind: "unknown" };\n` +
    `// @ts-expect-error missing ticket id\nconst missing: ResourceMutationEffect = { kind: "ticketChanged" };\n` +
    `void unknown; void missing;\n`,
  "utf8"
);
const program = ts.createProgram([fixturePath], { ...parsedConfig.options, noEmit: true });
const diagnostics = ts.getPreEmitDiagnostics(program).filter((diagnostic) => diagnostic.file?.fileName === fixturePath);
await rm(typeDir, { recursive: true, force: true });
assert.deepEqual(diagnostics.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")), []);

console.log("resource-catalogue.test.mjs: all assertions passed");
