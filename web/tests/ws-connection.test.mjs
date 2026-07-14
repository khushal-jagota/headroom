import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/ws.ts", import.meta.url), "utf8");
const sockets = [];
const timers = new Map();
const invalidations = [];
const statuses = [];
let now = 0;
let nextTimer = 1;
let reconciliations = 0;

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    sockets.push(this);
  }

  close() {}
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

const debug = { wsOpens: 0, cursor: 0, events: 0, flushes: 0, invalidations: {} };
globalThis.window = {
  location: { protocol: "http:", host: "panels.test" },
  setTimeout: setTimeoutFake,
  clearTimeout: clearTimeoutFake
};
globalThis.WebSocket = FakeWebSocket;
globalThis.__wsTestDeps = {
  ensureDebug: () => debug,
  invalidateCatalogueResources: (keys) => invalidations.push([...keys]),
  keysForEvent: (event) => [`ticket:${event.entity_id}`],
  reconcileSubscribedCatalogueResources: async () => {
    reconciliations += 1;
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
    'import { ensureDebug } from "./debug";',
    "const { ensureDebug } = globalThis.__wsTestDeps;"
  )
  .replace(
    /import \{[\s\S]*?\} from "\.\/resourceCatalogue";/,
    "const { invalidateCatalogueResources, keysForEvent, reconcileSubscribedCatalogueResources } = globalThis.__wsTestDeps;"
  )
  .replace(
    'import { writable } from "svelte/store";',
    "const { writable } = globalThis.__wsTestDeps;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-ws-connection-"));
const modulePath = join(dir, "ws.mjs");
await writeFile(modulePath, compiled, "utf8");
const { __eventCursor, startEventStream, stopEventStream } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

startEventStream({ debounceMs: 10, heartbeatMs: 100_000 });
assert.equal(sockets.length, 1);
sockets[0].onopen();
sockets[0].onmessage({
  data: JSON.stringify({
    events: [{ entity_id: "t_current" }],
    cursor: 1
  })
});
assert.equal(statuses.at(-1), "connected");
assert.equal(__eventCursor(), 1);

sockets[0].onclose();
assert.equal(statuses.at(-1), "reconnecting");
advance(500);
assert.equal(sockets.length, 2);
sockets[1].onopen();
sockets[1].onmessage({ data: JSON.stringify({ events: [], cursor: 1 }) });
assert.equal(statuses.at(-1), "connected");
assert.equal(reconciliations, 1);
const eventsAfterRecovery = debug.events;
const opensAfterRecovery = debug.wsOpens;

sockets[0].onopen();
sockets[0].onmessage({
  data: JSON.stringify({
    events: [{ entity_id: "t_stale" }],
    cursor: 99
  })
});
sockets[0].onclose();
advance(500);

assert.equal(statuses.at(-1), "connected");
assert.equal(__eventCursor(), 1);
assert.equal(reconciliations, 1);
assert.equal(debug.events, eventsAfterRecovery);
assert.equal(debug.wsOpens, opensAfterRecovery);
assert.equal(sockets.length, 2);
assert.ok(!invalidations.some((keys) => keys.includes("ticket:t_stale")));

stopEventStream();
console.log("ws-connection.test.mjs: all assertions passed");
