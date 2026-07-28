import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/queryCatalogue.ts", import.meta.url), "utf8");

const fetches = [];
globalThis.__queryCatalogueTestDeps = {
  fetchJson: async (path, options = {}) => {
    fetches.push({ path, options });
    return { path };
  },
  queryOptions: (options) => options
};

const executableSource = source
  .replace(
    'import { queryOptions } from "@tanstack/svelte-query";',
    "const { queryOptions } = globalThis.__queryCatalogueTestDeps;"
  )
  .replace(
    'import { fetchJson } from "./api";',
    "const { fetchJson } = globalThis.__queryCatalogueTestDeps;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-query-catalogue-"));
const modulePath = join(dir, "queryCatalogue.mjs");
await writeFile(modulePath, compiled, "utf8");
const { queries } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

const expectedEntries = [
  "board",
  "review",
  "todayDay",
  "backlogSprintItems",
  "ideas",
  "projects",
  "sprintSummaries",
  "sprintItems",
  "currentSprint",
  "ticket",
  "ticketConversationStartValues",
  "chiefConversationStartValues",
  "workerTypeManifests",
  "workers",
  "worker",
  "skillsHome"
];
assert.deepEqual(Object.keys(queries), expectedEntries);

// Every resource the browser reads: its query key and the path it comes from.
const catalogue = [
  [queries.board(), ["board"], "/api/board"],
  [queries.review(), ["review"], "/api/review"],
  [queries.todayDay(), ["day", "today"], "/api/day/today"],
  [queries.backlogSprintItems(), ["items", "backlog"], "/api/items?sprint_id=null"],
  [queries.ideas(), ["ideas"], "/api/ideas"],
  [queries.projects(), ["projects"], "/api/projects"],
  [queries.sprintSummaries(), ["sprints"], "/api/sprints"],
  [queries.sprintItems(), ["items"], "/api/items"],
  [queries.currentSprint(), ["sprint", "current"], "/api/sprint/current"],
  [queries.ticket("t_demo"), ["ticket", "t_demo"], "/api/tickets/t_demo"],
  [
    queries.ticketConversationStartValues("t_demo"),
    ["ticket", "t_demo", "conversation-start-values"],
    "/api/tickets/t_demo/conversation/start-values"
  ],
  [
    queries.chiefConversationStartValues(),
    ["chief", "conversation-start-values"],
    "/api/chief/conversation/start-values"
  ],
  [queries.workerTypeManifests(), ["worker-types"], "/api/worker-types"],
  [queries.workers(), ["workers"], "/api/workers"],
  [queries.worker("coding"), ["worker", "coding"], "/api/workers/coding"],
  [queries.skillsHome(), ["skills-home"], "/api/skills"]
];
assert.equal(catalogue.length, expectedEntries.length);
for (const [options, queryKey, path] of catalogue) {
  assert.deepEqual(options.queryKey, queryKey);
  const signal = new AbortController().signal;
  await options.queryFn({ signal });
  assert.equal(fetches.at(-1).path, path);
  assert.equal(fetches.at(-1).options.signal, signal);
}

// A parameterized id is carried whole in the key and URL-encoded in the path.
const awkwardTicket = queries.ticket("t_a/b?c");
assert.deepEqual(awkwardTicket.queryKey, ["ticket", "t_a/b?c"]);
await awkwardTicket.queryFn({ signal: new AbortController().signal });
assert.equal(fetches.at(-1).path, "/api/tickets/t_a%2Fb%3Fc");

const awkwardWorker = queries.worker("coding/a b");
assert.deepEqual(awkwardWorker.queryKey, ["worker", "coding/a b"]);
await awkwardWorker.queryFn({ signal: new AbortController().signal });
assert.equal(fetches.at(-1).path, "/api/workers/coding%2Fa%20b");

// The same resource asked for twice is the same identity.
assert.deepEqual(queries.ticket("t_demo").queryKey, queries.ticket("t_demo").queryKey);
assert.notDeepEqual(queries.ticket("t_demo").queryKey, queries.ticket("t_other").queryKey);

console.log("query-catalogue.test.mjs: all assertions passed");
