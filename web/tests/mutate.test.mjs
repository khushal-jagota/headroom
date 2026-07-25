import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/mutate.ts", import.meta.url), "utf8");

const requests = [];
const invalidations = [];
let fetchResult = { ok: true };
let fetchError = null;
let nextInvalidation = null;

globalThis.__mutateTestDeps = {
  fetchJson: async (path, options = {}) => {
    requests.push({ path, options });
    if (fetchError) throw fetchError;
    return fetchResult;
  },
  queryClient: {
    invalidateQueries: () => {
      invalidations.push(requests.length);
      return nextInvalidation || Promise.resolve();
    }
  }
};

const executableSource = source
  .replace(
    'import { fetchJson, type FetchOptions } from "./api";',
    "const { fetchJson } = globalThis.__mutateTestDeps;"
  )
  .replace(
    'import { queryClient } from "./queryClient";',
    "const { queryClient } = globalThis.__mutateTestDeps;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-mutate-"));
const modulePath = join(dir, "mutate.mjs");
await writeFile(modulePath, compiled, "utf8");
const { mutateJson } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

// A successful write sends the request, then invalidates every cached read.
fetchResult = { id: "t_written" };
const written = await mutateJson("/api/tickets/t_written", { method: "PATCH", body: { title: "x" } });
assert.deepEqual(written, fetchResult);
assert.deepEqual(requests.at(-1), {
  path: "/api/tickets/t_written",
  options: { method: "PATCH", body: { title: "x" } }
});
assert.equal(invalidations.length, 1);

// A write with no options still sends the request.
await mutateJson("/api/tickets/t_written/acknowledge-completed-response");
assert.deepEqual(requests.at(-1), { path: "/api/tickets/t_written/acknowledge-completed-response", options: {} });
assert.equal(invalidations.length, 2);

// A failed write changed nothing on the server, so it invalidates nothing and
// the caller sees the original error.
const failure = new Error("write rejected");
fetchError = failure;
await assert.rejects(
  mutateJson("/api/tickets/t_failed", { method: "POST" }),
  (error) => error === failure
);
assert.equal(invalidations.length, 2);
fetchError = null;

// Awaiting the write waits for the refetches the invalidation started, so a
// caller that acts afterwards is acting on fresh data.
let releaseInvalidation;
nextInvalidation = new Promise((resolve) => {
  releaseInvalidation = resolve;
});
let settled = false;
const pending = mutateJson("/api/tickets/t_awaited", { method: "POST" }).then(() => {
  settled = true;
});
await Promise.resolve();
await Promise.resolve();
assert.equal(settled, false, "the write does not settle before its invalidation does");
assert.equal(invalidations.length, 3);
releaseInvalidation();
await pending;
assert.equal(settled, true);
nextInvalidation = null;

console.log("mutate.test.mjs: all assertions passed");
