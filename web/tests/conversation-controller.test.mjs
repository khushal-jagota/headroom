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
    this.onSend?.(action);
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

function humanEnvelope(sequence, text) {
  return {
    ...connectionEnvelope(sequence, "ready"),
    type: "human_echo",
    payload: {
      clientMessageId: `restored-${sequence}`,
      prompt: { sessionId: "session-deferred", prompt: [{ type: "text", text }] },
    },
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
  const deferredPublications = [];
  let latestDeferredSnapshot = deferred.controller.snapshot();
  deferred.controller.subscribe((snapshot) => {
    latestDeferredSnapshot = snapshot;
    deferredPublications.push(structuredClone(snapshot));
  });
  let snapshotAtPromptSend = null;
  deferred.transports[1].onSend = (action) => {
    if (action.type === "prompt") snapshotAtPromptSend = structuredClone(latestDeferredSnapshot);
  };
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(1, "reset"));
  assert.equal(
    deferred.transports[1].sent.filter((action) => action.type === "prompt").length,
    0,
    "reset alone does not release the pending first prompt",
  );
  deferred.transports[1].callbacks.onEnvelope(humanEnvelope(2, "Restored before first demand"));
  assert.equal(deferred.controller.snapshot().session.messages.length, 0);
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(3, "ready"));
  const delivered = deferred.transports[1].sent.filter((action) => action.type === "prompt");
  assert.equal(delivered.length, 1);
  assert.deepEqual(delivered[0], {
    type: "prompt",
    employeeId: "ticket-deferred",
    clientMessageId: "client-1",
    prompt: [{ type: "text", text: "First demand" }],
    deliveryChoice: "normal",
  });
  assert.equal(
    snapshotAtPromptSend.session.messages.some((message) => message.id === "restored-2"),
    true,
    "the complete replay is committed before the deferred prompt reaches transport",
  );
  const replayPublicationIndex = deferredPublications.findIndex((snapshot) => (
    snapshot.session.messages.some((message) => message.id === "restored-2")
    && !snapshot.session.messages.some((message) => message.id === "client-1")
  ));
  const optimisticPublicationIndex = deferredPublications.findIndex((snapshot) => (
    snapshot.session.messages.some((message) => message.id === "client-1")
  ));
  assert.ok(replayPublicationIndex >= 0 && optimisticPublicationIndex > replayPublicationIndex);
  deferred.transports[1].callbacks.onEnvelope(connectionEnvelope(4, "ready"));
  assert.equal(
    deferred.transports[1].sent.filter((action) => action.type === "prompt").length,
    1,
    "a later ready cannot duplicate the pending prompt",
  );
  deferred.transports[1].callbacks.onClose(null);
  deferred.runReconnect();
  deferred.transports[2].callbacks.onOpen();
  deferred.transports[2].callbacks.onEnvelope(connectionEnvelope(5, "ready"));
  assert.equal(
    deferred.transports[2].sent.filter((action) => action.type === "prompt").length,
    0,
    "reconnect after delivery does not replay the prompt",
  );
  deferred.controller.dispose();

  const empty = controllerHarness(subject);
  empty.controller.attach();
  empty.transports[0].callbacks.onOpen();
  empty.transports[0].callbacks.onEnvelope(connectionEnvelope(1, "reset", {
    acpSessionId: null,
    payload: {
      state: "reset",
      detail: "New conversation",
      supportsSteer: false,
      resetBindingGeneration: 1,
    },
  }));
  empty.transports[0].callbacks.onEnvelope(connectionEnvelope(2, "ready", {
    acpSessionId: null,
    payload: { state: "ready", detail: "Ready", supportsSteer: false },
  }));
  assert.equal(empty.controller.prompt([{ type: "text", text: "Activate" }], "normal").ok, true);
  assert.deepEqual(empty.transports[0].sent.at(-1), {
    type: "prompt",
    employeeId: "ticket-deferred",
    clientMessageId: "client-1",
    prompt: [{ type: "text", text: "Activate" }],
    deliveryChoice: "normal",
  });
  empty.transports[0].callbacks.onEnvelope(connectionEnvelope(3, "reset"));
  empty.transports[0].callbacks.onEnvelope(connectionEnvelope(4, "ready"));
  assert.equal(empty.controller.snapshot().cursor.acpSessionId, "session-deferred");
  assert.equal(empty.controller.snapshot().connection.state, "ready");
  empty.controller.dispose();

  // A queued prompt lives only in the queue tray until it is actually sent: it must not echo into
  // the transcript at submit time, and the server's later human echo is what places it there.
  const queued = controllerHarness(subject);
  queued.controller.attach();
  queued.transports[0].callbacks.onOpen();
  queued.transports[0].callbacks.onEnvelope(connectionEnvelope(1, "reset"));
  queued.transports[0].callbacks.onEnvelope(connectionEnvelope(2, "ready"));
  queued.transports[0].callbacks.onEnvelope({
    ...connectionEnvelope(3, "ready"),
    type: "activity",
    payload: { state: "working", detail: "Working", sequence: 3 },
  });
  assert.equal(queued.controller.snapshot().activity?.state, "working", "an active turn is required to queue");
  const queuedResult = queued.controller.prompt([{ type: "text", text: "Queued line" }], "queue");
  assert.equal(queuedResult.ok, true);
  const queuedSend = queued.transports[0].sent.filter((action) => action.type === "prompt").at(-1);
  assert.equal(queuedSend.deliveryChoice, "queue", "the queue choice reaches transport");
  assert.equal(
    queued.controller.snapshot().session.messages.some((message) => message.id === queuedResult.clientMessageId),
    false,
    "a queued prompt does not echo into the transcript at submit time",
  );
  queued.transports[0].callbacks.onEnvelope({
    ...connectionEnvelope(4, "ready"),
    type: "human_echo",
    payload: {
      clientMessageId: queuedResult.clientMessageId,
      prompt: { sessionId: "session-deferred", prompt: [{ type: "text", text: "Queued line" }] },
    },
  });
  assert.equal(
    queued.controller.snapshot().session.messages.some((message) => message.id === queuedResult.clientMessageId),
    true,
    "the server human echo places the sent prompt into the transcript",
  );
  queued.controller.dispose();

  const reconnecting = controllerHarness(subject);
  reconnecting.controller.attach();
  reconnecting.transports[0].callbacks.onOpen();
  reconnecting.transports[0].callbacks.onClose(null);
  assert.deepEqual(
    reconnecting.controller.newConversation(),
    { ok: true },
    "New remains available while the old conversation is reconnecting",
  );
  assert.equal(reconnecting.transports.length, 2);
  reconnecting.transports[1].callbacks.onOpen();
  assert.deepEqual(reconnecting.transports[1].sent, [
    { type: "new_conversation", employeeId: "ticket-deferred" },
  ]);
  reconnecting.controller.dispose();

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
