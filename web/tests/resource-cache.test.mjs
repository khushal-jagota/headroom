import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/resources.svelte.ts", import.meta.url), "utf8");
const invalidationCounts = [];
globalThis.__resourceDebug = {
  countInvalidation: (key) => invalidationCounts.push(key)
};
globalThis.$state = (value) => value;

const executableSource = source.replace(
  'import { countInvalidation } from "./debug";',
  "const { countInvalidation } = globalThis.__resourceDebug;"
);
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-resource-cache-"));
const modulePath = join(dir, "resources.mjs");
await writeFile(modulePath, compiled, "utf8");
const { resource, peek, invalidateMany, refresh, __resourceStats } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

function deferred() {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}
async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

// Same-key first loads share one request and one value.
const dedupRequests = [];
const dedupFirst = deferred();
const dedupFetcher = (signal) => {
  dedupRequests.push(signal);
  return dedupFirst.promise;
};
const dedupA = resource("test:dedup", dedupFetcher);
const dedupB = resource("test:dedup", dedupFetcher);
assert.equal(dedupRequests.length, 1);
dedupFirst.resolve({ value: 1 });
await dedupFirst.promise;
await settle();
assert.deepEqual(dedupA.data, { value: 1 });
assert.equal(dedupA.data, dedupB.data);

// Forced refresh aborts/supersedes the old request and ignores its later completion.
const refreshRequests = [];
const refreshDeferred = [];
const refreshHandle = resource("test:supersede", (signal) => {
  const request = deferred();
  refreshRequests.push(signal);
  refreshDeferred.push(request);
  return request.promise;
});
refreshDeferred[0].resolve("initial");
await refreshDeferred[0].promise;
await settle();
const oldRefresh = refreshHandle.refresh();
const newRefresh = refreshHandle.refresh();
assert.equal(refreshRequests.length, 3);
assert.equal(refreshRequests[1].aborted, true);
refreshDeferred[1].resolve("stale completion");
await oldRefresh;
await settle();
assert.equal(refreshHandle.data, "initial");
refreshDeferred[2].resolve("new completion");
await newRefresh;
await settle();
assert.equal(refreshHandle.data, "new completion");

// Refresh failure preserves last-good data, marks stale, and keeps the exact error.
const structuredError = { code: "structured", detail: { exact: true } };
let failureRequest;
const failureHandle = resource("test:last-good", () => {
  failureRequest = deferred();
  return failureRequest.promise;
});
failureRequest.resolve("last good");
await failureRequest.promise;
await settle();
const failedRefresh = failureHandle.refresh();
failureRequest.reject(structuredError);
await assert.rejects(failedRefresh, (error) => error === structuredError);
await settle();
assert.equal(failureHandle.data, "last good");
assert.equal(failureHandle.error, structuredError);
assert.equal(failureHandle.stale, true);

// A first-load failure has no data and preserves the same structured error.
const firstError = new Error("first load");
const firstFailure = deferred();
const firstFailureHandle = resource("test:first-error", () => firstFailure.promise);
firstFailure.reject(firstError);
await settle();
assert.equal(firstFailureHandle.data, undefined);
assert.equal(firstFailureHandle.error, firstError);
assert.equal(firstFailureHandle.stale, false);

// Subscribed invalidation refetches once even when the batch repeats a key.
const batchRequests = [];
const batchDeferred = [];
const batchHandle = resource("test:batch", () => {
  const request = deferred();
  batchRequests.push(request);
  batchDeferred.push(request);
  return request.promise;
});
batchDeferred[0].resolve("batch initial");
await batchDeferred[0].promise;
await settle();
invalidateMany(["test:batch", "test:batch"], "batch");
assert.equal(batchRequests.length, 2);
assert.deepEqual(invalidationCounts.slice(-1), ["test:batch"]);
batchDeferred[1].resolve("batch refreshed");
await batchDeferred[1].promise;
await settle();
assert.equal(batchHandle.data, "batch refreshed");

// An unsubscribed entry is marked stale without a read; its next subscriber reads.
const dormantRequests = [];
const dormantDeferred = [];
const dormantFetcher = () => {
  const request = deferred();
  dormantRequests.push(request);
  dormantDeferred.push(request);
  return request.promise;
};
const dormant = resource("test:dormant", dormantFetcher);
dormantDeferred[0].resolve("dormant initial");
await dormantDeferred[0].promise;
await settle();
dormant.dispose();
dormant.dispose();
invalidateMany(["test:dormant"], "dormant");
assert.equal(dormantRequests.length, 1);
assert.equal(__resourceStats().find((entry) => entry.key === "test:dormant").stale, true);
const reopened = resource("test:dormant", dormantFetcher);
assert.equal(dormantRequests.length, 2);
dormantDeferred[1].resolve("dormant reopened");
await dormantDeferred[1].promise;
await settle();
assert.equal(reopened.data, "dormant reopened");
assert.equal(__resourceStats().find((entry) => entry.key === "test:dormant").subscribers, 1);

// Peek is read-only and missing-key invalidation/refresh performs no request.
assert.equal(peek("test:dormant"), "dormant reopened");
assert.equal(peek("test:missing"), undefined);
const statsBeforePeek = __resourceStats().find((entry) => entry.key === "test:dormant");
peek("test:dormant");
assert.deepEqual(__resourceStats().find((entry) => entry.key === "test:dormant"), statsBeforePeek);
invalidateMany(["test:missing"], "missing");
assert.equal(refresh("test:missing"), undefined);

dedupA.dispose();
dedupB.dispose();
refreshHandle.dispose();
failureHandle.dispose();
firstFailureHandle.dispose();
batchHandle.dispose();
reopened.dispose();

console.log("resource-cache.test.mjs: all assertions passed");
