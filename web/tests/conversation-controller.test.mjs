import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";

const webRoot = new URL("..", import.meta.url).pathname;
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp07-controller-"));
const entryPath = join(webRoot, "tests", `.acp07-controller-subject-${process.pid}.ts`);

class FakeTransport {
  constructor(callbacks) {
    this.callbacks = callbacks;
    this.sent = [];
    this.openCount = 0;
    this.closeCount = 0;
    this.detachCount = 0;
  }

  open() {
    this.openCount += 1;
  }

  send(action) {
    this.sent.push(structuredClone(action));
    return { ok: true };
  }

  close() {
    this.closeCount += 1;
  }

  detach() {
    this.detachCount += 1;
  }
}

function connectionEnvelope(sequence, state, overrides = {}) {
  return {
    wireVersion: 1,
    employeeId: "ticket-deferred",
    entityKind: "ticket",
    entityId: "ticket-deferred",
    acpSessionId: "session-deferred",
    bindingGeneration: 1,
    sequence,
    type: "connection",
    payload: {
      state,
      detail: state === "ready" ? "Ready" : "Loaded",
      supportsSteer: true,
      ...(state === "reset" ? { resetBindingGeneration: 1 } : {}),
    },
    ...overrides,
  };
}

function controllerHarness(subject, { deferInitialAttach = false } = {}) {
  const transports = [];
  const timers = new Map();
  let nextTimer = 1;
  let nextClientMessage = 1;
  const controller = subject.createConversationController({
    employeeId: "ticket-deferred",
    deferInitialAttach,
    transportFactory: (callbacks) => {
      const transport = new FakeTransport(callbacks);
      transports.push(transport);
      return transport;
    },
    reconnectDelayMs: 25,
    setTimer: (callback) => {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    now: () => 1000,
    fallbackId: (turn, role, segment) => `fallback-${turn}-${role}-${segment}`,
    clientMessageId: () => `client-${nextClientMessage++}`,
  });
  return {
    controller,
    transports,
    runReconnect() {
      const next = timers.entries().next().value;
      assert.ok(next, "a reconnect must be scheduled");
      timers.delete(next[0]);
      next[1]();
    },
  };
}

try {
  await writeFile(entryPath, "export * from '../src/lib/acp/conversationController';", "utf8");
  await build({
    root: webRoot,
    configFile: false,
    logLevel: "silent",
    build: {
      emptyOutDir: true,
      lib: { entry: entryPath, formats: ["es"], fileName: () => "subject.js" },
      minify: false,
      outDir: temporaryDirectory,
    },
  });
  const subject = await import(join(temporaryDirectory, "subject.js"));

  const eager = controllerHarness(subject);
  assert.equal(eager.controller.prompt([{ type: "text", text: "Too early" }], "normal").ok, false);
  assert.equal(eager.transports.length, 0, "ordinary controllers do not attach on an early prompt");
  eager.controller.attach();
  eager.controller.attach();
  assert.equal(eager.transports.length, 1, "ordinary attach remains idempotent");
  eager.transports[0].callbacks.onOpen();
  eager.transports[0].callbacks.onEnvelope(connectionEnvelope(1, "reset"));
  eager.transports[0].callbacks.onEnvelope(connectionEnvelope(2, "ready"));
  assert.equal(eager.controller.prompt([{ type: "text", text: "Eager ready" }], "normal").ok, true);
  assert.equal(eager.transports[0].sent.filter((action) => action.type === "prompt").length, 1);
  eager.controller.dispose();

  const deferred = controllerHarness(subject, { deferInitialAttach: true });
  assert.equal(deferred.transports.length, 0, "a pristine Kickoff controller opens no transport");
  const first = deferred.controller.prompt([{ type: "text", text: "First demand" }], "normal");
  assert.deepEqual(first, { ok: true, clientMessageId: "client-1" });
  assert.equal(deferred.transports.length, 1, "the first explicit prompt attaches");
  assert.equal(deferred.transports[0].sent.length, 0, "the prompt waits for an admitted ready");
  assert.deepEqual(
    deferred.controller.prompt([{ type: "text", text: "Second demand" }], "normal"),
    { ok: false, reason: "The first message is waiting for the conversation to connect" },
  );

  deferred.transports[0].callbacks.onOpen();
  assert.deepEqual(deferred.transports[0].sent, [{ type: "attach", employeeId: "ticket-deferred" }]);
  deferred.transports[0].callbacks.onClose(null);
  deferred.runReconnect();
  assert.equal(deferred.transports.length, 2);
  deferred.transports[1].callbacks.onOpen();
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(1, "reset"));
  assert.equal(
    deferred.transports[1].sent.filter((action) => action.type === "prompt").length,
    0,
    "reset alone does not release the pending first prompt",
  );
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(2, "ready"));
  const delivered = deferred.transports[1].sent.filter((action) => action.type === "prompt");
  assert.equal(delivered.length, 1);
  assert.deepEqual(delivered[0], {
    type: "prompt",
    employeeId: "ticket-deferred",
    clientMessageId: "client-1",
    prompt: {
      sessionId: "session-deferred",
      prompt: [{ type: "text", text: "First demand" }],
    },
    deliveryChoice: "normal",
  });
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(3, "ready"));
  assert.equal(
    deferred.transports[1].sent.filter((action) => action.type === "prompt").length,
    1,
    "a later ready cannot duplicate the pending prompt",
  );
  deferred.transports[1].callbacks.onClose(null);
  deferred.runReconnect();
  deferred.transports[2].callbacks.onOpen();
  deferred.transports[2].callbacks.onEnvelope(connectionEnvelope(4, "ready"));
  assert.equal(
    deferred.transports[2].sent.filter((action) => action.type === "prompt").length,
    0,
    "reconnect after delivery does not replay the prompt",
  );
  deferred.controller.dispose();

  const disposed = controllerHarness(subject, { deferInitialAttach: true });
  assert.equal(disposed.controller.prompt([{ type: "text", text: "Drop me" }], "normal").ok, true);
  const staleTransport = disposed.transports[0];
  disposed.controller.dispose();
  staleTransport.callbacks.onOpen();
  staleTransport.callbacks.onEnvelope(connectionEnvelope(1, "reset"));
  staleTransport.callbacks.onEnvelope(connectionEnvelope(2, "ready"));
  assert.equal(
    staleTransport.sent.filter((action) => action.type === "prompt").length,
    0,
    "dispose drops the deferred prompt",
  );
} finally {
  await rm(entryPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

console.log("conversation-controller.test.mjs: all assertions passed");
