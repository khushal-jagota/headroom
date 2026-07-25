import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/changeStream.ts", import.meta.url), "utf8");

const streams = [];
const timers = new Map();
const statuses = [];
let invalidations = 0;
let now = 0;
let nextTimer = 1;

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.closed = false;
    streams.push(this);
  }

  close() {
    this.closed = true;
  }
}

function setTimeoutFake(callback, delay) {
  const id = nextTimer++;
  timers.set(id, { callback, due: now + delay });
  return id;
}

function clearTimeoutFake(id) {
  timers.delete(id);
}

function advance(ms) {
  const target = now + ms;
  while (true) {
    const next = [...timers.entries()]
      .filter(([, timer]) => timer.due <= target)
      .sort((left, right) => left[1].due - right[1].due || left[0] - right[0])[0];
    if (!next) break;
    const [id, timer] = next;
    timers.delete(id);
    now = timer.due;
    timer.callback();
  }
  now = target;
}

const debug = { sseOpens: 0, flushes: 0 };
globalThis.window = {
  location: { protocol: "http:", host: "panels.test" },
  setTimeout: setTimeoutFake,
  clearTimeout: clearTimeoutFake
};
globalThis.EventSource = FakeEventSource;
globalThis.__changeStreamTestDeps = {
  ensureDebug: () => debug,
  queryClient: {
    invalidateQueries: () => {
      invalidations += 1;
      return Promise.resolve();
    }
  },
  writable: (initial) => {
    statuses.push(initial);
    return {
      set(value) {
        statuses.push(value);
      },
      subscribe() {
        return () => undefined;
      }
    };
  }
};

const executableSource = source
  .replace(
    'import { writable } from "svelte/store";',
    "const { writable } = globalThis.__changeStreamTestDeps;"
  )
  .replace(
    'import { ensureDebug } from "./debug";',
    "const { ensureDebug } = globalThis.__changeStreamTestDeps;"
  )
  .replace(
    'import { queryClient } from "./queryClient";',
    "const { queryClient } = globalThis.__changeStreamTestDeps;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-change-stream-"));
const modulePath = join(dir, "changeStream.mjs");
await writeFile(modulePath, compiled, "utf8");
const { startChangeStream, stopChangeStream } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

// The stream is the plain SSE endpoint, and the pill starts out reconnecting.
startChangeStream();
assert.equal(streams.length, 1);
assert.equal(streams[0].url, "/api/changes");
assert.equal(statuses.at(-1), "reconnecting");
assert.equal(invalidations, 0);

// Starting twice keeps the one stream.
startChangeStream();
assert.equal(streams.length, 1);

// An open connection reconciles once: status connected plus one invalidation.
streams[0].onopen();
assert.equal(statuses.at(-1), "connected");
assert.equal(invalidations, 1);
assert.equal(debug.sseOpens, 1);
assert.equal(debug.flushes, 1);

// A burst of change frames collapses into a single trailing invalidation, and
// nothing happens until the 250ms window has passed.
streams[0].onmessage({ data: "change" });
streams[0].onmessage({ data: "change" });
advance(249);
assert.equal(invalidations, 1);
streams[0].onmessage({ data: "change" });
advance(249);
assert.equal(invalidations, 1, "each frame restarts the trailing window");
advance(1);
assert.equal(invalidations, 2);
assert.equal(debug.flushes, 2);

// A later frame gets its own flush.
streams[0].onmessage({ data: "change" });
advance(250);
assert.equal(invalidations, 3);

// A dropped connection reads as reconnecting; EventSource retries by itself, so
// no new stream is opened here.
streams[0].onerror();
assert.equal(statuses.at(-1), "reconnecting");
assert.equal(streams.length, 1);

// The same stream object reopening reconciles again.
streams[0].onopen();
assert.equal(statuses.at(-1), "connected");
assert.equal(debug.sseOpens, 2);
assert.equal(invalidations, 4);

// Stopping closes the stream, drops a pending flush, and resets the pill.
streams[0].onmessage({ data: "change" });
stopChangeStream();
assert.equal(streams[0].closed, true);
assert.equal(statuses.at(-1), "reconnecting");
advance(250);
assert.equal(invalidations, 4, "a pending flush is dropped when the stream stops");

// A stopped stream's late callbacks are ignored.
streams[0].onopen();
streams[0].onmessage({ data: "change" });
advance(250);
assert.equal(invalidations, 4);
assert.equal(debug.sseOpens, 2);

console.log("change-stream.test.mjs: all assertions passed");
