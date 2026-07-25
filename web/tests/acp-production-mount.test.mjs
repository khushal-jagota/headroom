import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { compile } from "svelte/compiler";
import { build } from "vite";

const webRoot = new URL("..", import.meta.url).pathname;
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp-production-mount-"));
const entryPath = join(webRoot, "tests", `.acp-production-subject-${process.pid}.ts`);

const wrapperPath = new URL("../src/components/AcpConversation.svelte", import.meta.url);
const wrapperSource = await readFile(wrapperPath, "utf8");
assert.deepEqual(
  compile(wrapperSource, { filename: "AcpConversation.svelte", generate: "client" }).warnings,
  [],
);
assert.match(wrapperSource, /createProductionConversationController\(stableEmployeeId\)/);
assert.match(
  wrapperSource,
  /createProductionConversationController\(stableEmployeeId, undefined, true\)/,
);
assert.match(wrapperSource, /<AcpConversationPane \{controller\} \{employeeLabel\} \{deferInitialAttach\}/);
assert.doesNotMatch(wrapperSource, /<style>|ChatPanel|ChiefNeutralPane|relayChief|\/api\/chat|\/api\/relay/);

const appSource = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
assert.match(appSource, /startChangeStream\(\);/);
assert.doesNotMatch(appSource, /\/api\/meta/);
assert.doesNotMatch(
  appSource,
  /capabilities|relay_chief_enabled|resolveRelayChiefFromMeta|markRelayChiefMetaError/
);

const routeExpectations = [
  ["BoardRoute.svelte", /employeeId=\{chiefOfStaffEntityId\}/, /employeeLabel="Chief of Staff"/],
  ["TicketRoute.svelte", /employeeId=\{stableId\}/, /employeeLabel=\{/],
  ["ChiefOfStaffRoute.svelte", /employeeId=\{entityId\}/, /employeeLabel="Chief of Staff"/],
];
for (const [fileName, identityPattern, labelPattern] of routeExpectations) {
  const source = await readFile(new URL(`../src/routes/${fileName}`, import.meta.url), "utf8");
  assert.match(source, /AcpConversation/);
  assert.match(source, identityPattern);
  assert.match(source, labelPattern);
  assert.doesNotMatch(
    source,
    /ChatPanel|ChiefNeutralPane|relayChief|retryRelayChiefMeta|chatGatewayStatus|\/api\/chat|\/api\/relay/,
  );
}

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
  }
  send(value) {
    this.sent.push(JSON.parse(value));
  }
  close() {}
  open() {
    this.onopen?.();
  }
  feed(value) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }
}

try {
  await writeFile(
    entryPath,
    "export * from '../src/lib/acp/productionConversation';",
  );
  await build({
    root: webRoot,
    logLevel: "silent",
    build: {
      emptyOutDir: true,
      lib: { entry: entryPath, formats: ["es"], fileName: () => "subject.js" },
      minify: false,
      outDir: temporaryDirectory,
    },
  });
  const subject = await import(join(temporaryDirectory, "subject.js"));
  assert.equal(
    subject.productionConversationUrl({ protocol: "https:", host: "panels.test" }),
    "wss://panels.test/api/conversation",
  );
  assert.equal(
    subject.productionConversationUrl({ protocol: "http:", host: "localhost:4173" }),
    "ws://localhost:4173/api/conversation",
  );
  assert.equal(subject.PRODUCTION_CONVERSATION_RECONNECT_DELAY_MS, 500);

  const sockets = [];
  const timers = [];
  const generatedIds = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
  ];
  const controller = subject.createProductionConversationController("ticket-production", {
    location: { protocol: "https:", host: "panels.test" },
    socketFactory: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    randomUUID: () => generatedIds.shift(),
    setTimer: (callback, delayMs) => {
      timers.push({ callback, delayMs });
      return timers.length;
    },
    clearTimer: () => undefined,
    now: () => 1000,
  });
  controller.attach();
  assert.equal(sockets[0].url, "wss://panels.test/api/conversation");
  sockets[0].open();
  assert.deepEqual(sockets[0].sent[0], {
    type: "attach",
    employeeId: "ticket-production",
  });
  const base = {
    wireVersion: 1,
    employeeId: "ticket-production",
    entityKind: "ticket",
    entityId: "ticket-production",
    acpSessionId: "session-production",
    bindingGeneration: 1,
  };
  sockets[0].feed({
    ...base,
    sequence: 1,
    type: "connection",
    payload: {
      state: "reset",
      detail: "Loaded",
      supportsSteer: true,
      resetBindingGeneration: 1,
    },
  });
  sockets[0].feed({
    ...base,
    sequence: 2,
    type: "connection",
    payload: { state: "ready", detail: "Ready", supportsSteer: true },
  });
  const result = controller.prompt([{ type: "text", text: "Hello" }], "normal");
  assert.equal(result.clientMessageId, "00000000-0000-4000-8000-000000000001");
  assert.equal(sockets[0].sent.at(-1).clientMessageId, result.clientMessageId);
  assert.equal("browserId" in sockets[0].sent[0], false);
  sockets[0].onclose?.();
  assert.equal(timers[0].delayMs, 500);
  controller.dispose();

  const deferredController = subject.createProductionConversationController(
    "ticket-deferred-production",
    {
      location: { protocol: "https:", host: "panels.test" },
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket;
      },
      randomUUID: () => generatedIds.shift(),
      setTimer: (callback, delayMs) => {
        timers.push({ callback, delayMs });
        return timers.length;
      },
      clearTimer: () => undefined,
      now: () => 1000,
    },
    true,
  );
  assert.equal(sockets.length, 1, "a deferred production controller opens no socket on creation");
  const pending = deferredController.prompt([{ type: "text", text: "First demand" }], "normal");
  assert.deepEqual(pending, {
    ok: true,
    clientMessageId: "00000000-0000-4000-8000-000000000002",
  });
  assert.equal(sockets.length, 2, "the first prompt opens the deferred socket");
  assert.equal(
    deferredController.prompt([{ type: "text", text: "Second demand" }], "normal").ok,
    false,
  );
  sockets[1].open();
  assert.deepEqual(sockets[1].sent[0], {
    type: "attach",
    employeeId: "ticket-deferred-production",
  });
  const deferredBase = {
    wireVersion: 1,
    employeeId: "ticket-deferred-production",
    entityKind: "ticket",
    entityId: "ticket-deferred-production",
    acpSessionId: "session-deferred-production",
    bindingGeneration: 1,
  };
  sockets[1].feed({
    ...deferredBase,
    sequence: 1,
    type: "connection",
    payload: {
      state: "reset",
      detail: "Loaded",
      supportsSteer: true,
      resetBindingGeneration: 1,
    },
  });
  assert.equal(sockets[1].sent.filter((action) => action.type === "prompt").length, 0);
  sockets[1].feed({
    ...deferredBase,
    sequence: 2,
    type: "connection",
    payload: { state: "ready", detail: "Ready", supportsSteer: true },
  });
  assert.deepEqual(sockets[1].sent.at(-1), {
    type: "prompt",
    employeeId: "ticket-deferred-production",
    clientMessageId: "00000000-0000-4000-8000-000000000002",
    prompt: [{ type: "text", text: "First demand" }],
    deliveryChoice: "normal",
  });
  deferredController.dispose();
} finally {
  await rm(entryPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

console.log("acp-production-mount.test.mjs: all assertions passed");
