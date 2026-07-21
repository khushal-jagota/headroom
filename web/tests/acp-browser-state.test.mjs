import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";

const webRoot = new URL("..", import.meta.url).pathname;
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp-browser-state-"));
const entryPath = join(webRoot, "tests", `.acp-browser-subject-${process.pid}.ts`);
const liveReplayFixture = JSON.parse(
  await readFile(new URL("../../tests/fixtures/acp/browser-live-replay-v1.json", import.meta.url), "utf8"),
);
const envelopeStatesFixture = JSON.parse(
  await readFile(new URL("../../tests/fixtures/acp/browser-envelope-states-v1.json", import.meta.url), "utf8"),
);
const controllerCasesFixture = JSON.parse(
  await readFile(new URL("../../tests/fixtures/acp/browser-controller-cases-v1.json", import.meta.url), "utf8"),
);

class FakeSocket {
  constructor(url, { failPermissionSend = false } = {}) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    this.failPermissionSend = failPermissionSend;
  }
  send(value) {
    const action = JSON.parse(value);
    if (this.failPermissionSend && action.type === "permission_response") {
      throw new Error("permission response send failed");
    }
    this.sent.push(action);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  feed(value) {
    this.onmessage?.({ data: typeof value === "string" ? value : JSON.stringify(value) });
  }
}

try {
  await writeFile(
    entryPath,
    [
      "export * from '../src/lib/acp/panelsTransport';",
      "export * from '../src/lib/acp/conversationState';",
      "export * from '../src/lib/acp/conversationController';",
      "export * from '../src/lib/acp/lineDiff';",
    ].join("\n"),
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
  runStateAssertions(subject, liveReplayFixture.live);
  runControllerAssertions(subject, liveReplayFixture, envelopeStatesFixture, controllerCasesFixture);
} finally {
  await rm(entryPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

function baseEnvelope(sequence, type, payload, overrides = {}) {
  return {
    wireVersion: 1,
    employeeId: "employee-1",
    entityKind: "ticket",
    entityId: "ticket-1",
    acpSessionId: "session-1",
    bindingGeneration: 1,
    sequence,
    type,
    payload,
    ...overrides,
  };
}

function updateEnvelope(sequence, update) {
  return baseEnvelope(sequence, "acp_session_update", {
    sessionId: "session-1",
    update,
  });
}

function runStateAssertions(subject, fixtureStream) {
  const dependencies = {
    now: () => 1000,
    fallbackId: (turn, role, segment) => `fallback-${turn}-${role}-${segment}`,
  };
  let state = subject.createConversationState("employee-1");
  const reduce = (envelope) => {
    state = subject.reduceConversationState(
      state,
      { kind: "server_envelope", envelope },
      dependencies,
    );
  };

  for (const envelope of fixtureStream.filter((item) => item.type === "acp_session_update")) {
    reduce(envelope);
  }
  reduce(baseEnvelope(9, "programmatic_prompt", {
    promptId: "worker-prompt-1",
    source: "worker",
    prompt: {
      sessionId: "session-1",
      prompt: [{ type: "text", text: "Automatic Employee prompt" }],
    },
  }));

  const snapshot = subject.projectConversationSnapshot(state);
  assert.equal(snapshot.programmaticPrompts["worker-prompt-1"].payload.source, "worker");
  assert.equal(snapshot.programmaticPrompts["worker-prompt-1"].payload.prompt.prompt[0].text, "Automatic Employee prompt");
  assert.deepEqual(snapshot.timeline.at(-1), { kind: "programmatic_prompt", promptId: "worker-prompt-1" });
  const thoughtPart = snapshot.session.messages
    .flatMap((message) => message.parts)
    .find((part) => part.type === "thought");
  assert.equal(thoughtPart.type, "thought");
  assert.equal(thoughtPart.expanded, false);
  const assistantText = snapshot.session.messages
    .filter((message) => message.role === "agent")
    .flatMap((message) => message.parts)
    .filter((part) => part.type === "content")
    .flatMap((part) => part.content)
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
  assert.equal(assistantText.includes("private reasoning"), false);
  assert.equal(assistantText, "The answer remains public.");
  assert.deepEqual(snapshot.session.plan.map((entry) => entry.content), ["Replacement snapshot"]);
  assert.equal(Object.keys(snapshot.session.pendingToolCalls).length, 1);
  assert.equal(snapshot.session.pendingToolCalls["tool-1"].status, "completed");
  assert.equal(snapshot.session.pendingToolCalls["tool-1"].content.length, 2);
  const agentParts = snapshot.session.messages
    .find((message) => message.parts.some((part) => part.type === "thought"))
    .parts.map((part) => part.type);
  assert.deepEqual(agentParts, ["thought", "content", "tool_calls", "plan"]);
  assert.equal(snapshot.session.messages.flatMap((message) => message.parts).filter((part) => part.type === "plan").length, 1);
  assert.equal(snapshot.currentModeId, "focus");
  assert.equal(snapshot.session.configOptions[0].id, "model");
  assert.equal(snapshot.sessionInfo.title, "Hermes fixture");
  assert.equal(snapshot.unsupportedAgentContent.length, 0);

  assert.throws(() => snapshot.session.messages.push({}), TypeError);
  assert.throws(() => {
    snapshot.session.pendingToolCalls["tool-1"].status = "failed";
  }, TypeError);
  const laterSnapshot = subject.projectConversationSnapshot(state);
  assert.equal(laterSnapshot.session.pendingToolCalls["tool-1"].status, "completed");

  state = subject.reduceConversationState(state, {
    kind: "tool_expanded",
    toolCallId: "tool-1",
    expanded: true,
  }, dependencies);
  reduce(updateEnvelope(99, {
    sessionUpdate: "tool_call_update",
    toolCallId: "tool-1",
    status: "in_progress",
  }));
  assert.equal(subject.projectConversationSnapshot(state).session.pendingToolCalls["tool-1"].expanded, true);
  assert.equal(subject.projectConversationSnapshot(state).session.pendingToolCalls["tool-1"].content.length, 2);

  let queuedBoundaryState = subject.createConversationState("employee-1");
  const reduceQueuedBoundary = (transition) => {
    queuedBoundaryState = subject.reduceConversationState(
      queuedBoundaryState,
      transition,
      dependencies,
    );
  };
  reduceQueuedBoundary({
    kind: "optimistic_prompt",
    clientMessageId: "queued-successor",
    prompt: {
      sessionId: "session-1",
      prompt: [{ type: "text", text: "Queued successor" }],
    },
  });
  reduceQueuedBoundary({
    kind: "server_envelope",
    envelope: baseEnvelope(1, "human_echo", {
      clientMessageId: "queued-successor",
      prompt: {
        sessionId: "session-1",
        prompt: [{ type: "text", text: "Queued successor" }],
      },
    }),
  });
  reduceQueuedBoundary({
    kind: "server_envelope",
    envelope: updateEnvelope(2, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "BASE TURN COMPLETE" },
    }),
  });
  reduceQueuedBoundary({
    kind: "server_envelope",
    envelope: baseEnvelope(3, "activity", {
      state: "idle",
      detail: "Idle",
      sequence: 3,
    }),
  });
  reduceQueuedBoundary({
    kind: "server_envelope",
    envelope: baseEnvelope(4, "delivery_receipt", {
      clientMessageId: "queued-successor",
      choice: "queue",
      state: "started",
    }),
  });
  reduceQueuedBoundary({
    kind: "server_envelope",
    envelope: updateEnvelope(5, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "QUEUED MESSAGE DELIVERED." },
    }),
  });
  const queuedAgentMessages = subject.projectConversationSnapshot(queuedBoundaryState).session.messages
    .filter((message) => message.role === "agent")
    .map((message) => ({
      id: message.id,
      text: message.parts.flatMap((part) => part.type === "content" ? part.content : [])
        .filter((block) => block.type === "text")
        .map((block) => block.text)
        .join(""),
    }));
  assert.equal(queuedAgentMessages.length, 2);
  assert.notEqual(queuedAgentMessages[0].id, queuedAgentMessages[1].id);
  assert.deepEqual(queuedAgentMessages.map((message) => message.text), [
    "BASE TURN COMPLETE",
    "QUEUED MESSAGE DELIVERED.",
  ]);

  let sendNowBoundaryState = subject.createConversationState("employee-1");
  for (const envelope of [
    updateEnvelope(1, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "Interrupted response" },
    }),
    baseEnvelope(2, "delivery_receipt", {
      clientMessageId: "send-now-successor",
      choice: "send_now",
      state: "interrupted",
      reason: "Replaced by Send Now",
    }),
    updateEnvelope(3, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "Send Now response" },
    }),
  ]) {
    sendNowBoundaryState = subject.reduceConversationState(
      sendNowBoundaryState,
      { kind: "server_envelope", envelope },
      dependencies,
    );
  }
  const sendNowAgentMessages = subject.projectConversationSnapshot(sendNowBoundaryState).session.messages
    .filter((message) => message.role === "agent")
    .map((message) => message.parts.flatMap((part) => part.type === "content" ? part.content : [])
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join(""));
  assert.deepEqual(sendNowAgentMessages, ["Interrupted response", "Send Now response"]);

  let recoveredSendNowState = subject.createConversationState("employee-1");
  for (const envelope of [
    baseEnvelope(1, "connection", {
      state: "reset",
      detail: "Conversation runtime recovered",
      supportsSteer: false,
    }),
    updateEnvelope(2, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "Operation interrupted." },
    }),
    baseEnvelope(3, "connection", {
      state: "ready",
      detail: "Ready",
      supportsSteer: false,
    }),
    baseEnvelope(4, "human_echo", {
      clientMessageId: "retained-fifo",
      prompt: {
        sessionId: "session-1",
        prompt: [{ type: "text", text: "Retained FIFO prompt" }],
      },
    }),
    baseEnvelope(5, "human_echo", {
      clientMessageId: "send-now-successor",
      prompt: {
        sessionId: "session-1",
        prompt: [{ type: "text", text: "Reply exactly SEND NOW RECOVERY READY." }],
      },
    }),
    baseEnvelope(6, "queue_snapshot", {
      items: [{
        clientMessageId: "retained-fifo",
        prompt: {
          sessionId: "session-1",
          prompt: [{ type: "text", text: "Retained FIFO prompt" }],
        },
        enqueueSequence: 1,
        enqueuedAt: 10,
      }],
    }),
    baseEnvelope(7, "delivery_receipt", {
      clientMessageId: "send-now-successor",
      choice: "send_now",
      state: "started",
    }),
    updateEnvelope(8, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "SEND NOW RECOVERY READY." },
    }),
  ]) {
    recoveredSendNowState = subject.reduceConversationState(
      recoveredSendNowState,
      { kind: "server_envelope", envelope },
      dependencies,
    );
  }
  const recoveredSendNowMessages = subject.projectConversationSnapshot(recoveredSendNowState).session.messages
    .map((message) => ({
      role: message.role,
      text: message.parts.flatMap((part) => part.type === "content" ? part.content : [])
        .filter((block) => block.type === "text")
        .map((block) => block.text)
        .join(""),
    }));
  assert.deepEqual(recoveredSendNowMessages, [
    { role: "agent", text: "Operation interrupted." },
    { role: "user", text: "Retained FIFO prompt" },
    { role: "user", text: "Reply exactly SEND NOW RECOVERY READY." },
    { role: "agent", text: "SEND NOW RECOVERY READY." },
  ]);
  assert.deepEqual(
    recoveredSendNowMessages.filter((message) => message.text !== "Retained FIFO prompt"),
    [
      { role: "agent", text: "Operation interrupted." },
      { role: "user", text: "Reply exactly SEND NOW RECOVERY READY." },
      { role: "agent", text: "SEND NOW RECOVERY READY." },
    ],
  );

  let nonTerminalBoundaryState = subject.createConversationState("employee-1");
  for (const envelope of [
    updateEnvelope(1, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "one " },
    }),
    baseEnvelope(2, "activity", { state: "thinking", detail: "Thinking", sequence: 2 }),
    baseEnvelope(3, "activity", { state: "working", detail: "Working", sequence: 3 }),
    baseEnvelope(4, "delivery_receipt", {
      clientMessageId: "steered-prompt",
      choice: "steer",
      state: "accepted",
    }),
    baseEnvelope(5, "delivery_receipt", {
      clientMessageId: "queued-prompt",
      choice: "queue",
      state: "queued",
      queuePosition: 1,
    }),
    baseEnvelope(6, "delivery_receipt", {
      clientMessageId: "queued-prompt",
      choice: "queue",
      state: "started",
    }),
    updateEnvelope(7, {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "response" },
    }),
  ]) {
    nonTerminalBoundaryState = subject.reduceConversationState(
      nonTerminalBoundaryState,
      { kind: "server_envelope", envelope },
      dependencies,
    );
  }
  const nonTerminalAgentMessages = subject.projectConversationSnapshot(nonTerminalBoundaryState).session.messages
    .filter((message) => message.role === "agent");
  assert.equal(nonTerminalAgentMessages.length, 1);
  assert.equal(
    nonTerminalAgentMessages[0].parts.flatMap((part) => part.type === "content" ? part.content : [])
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join(""),
    "one response",
  );

  assert.deepEqual(subject.lineDiff("a\nb\n", "a\nc\n"), [
    { kind: "context", text: "a", oldLine: 1, newLine: 1 },
    { kind: "delete", text: "b", oldLine: 2, newLine: null },
    { kind: "add", text: "c", oldLine: null, newLine: 2 },
    { kind: "context", text: "", oldLine: 3, newLine: 3 },
  ]);
}

function controllerHarness(subject, { failPermissionSend = false } = {}) {
  const sockets = [];
  const timers = new Map();
  let nextTimer = 1;
  let nextClient = 1;
  const controller = subject.createConversationController({
    employeeId: "employee-browser",
    transportFactory: (callbacks) => subject.createPanelsTransport({
      url: "ws://panels.test/acp",
      socketFactory: (url) => {
        const socket = new FakeSocket(url, { failPermissionSend });
        sockets.push(socket);
        return socket;
      },
      ...callbacks,
    }),
    reconnectDelayMs: 10,
    setTimer: (callback) => {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    now: () => 1000,
    fallbackId: (turn, role, segment) => `fallback-${turn}-${role}-${segment}`,
    clientMessageId: () => `client-${nextClient++}`,
  });
  return {
    controller,
    sockets,
    runTimer() {
      const next = timers.entries().next().value;
      assert.ok(next, "a reconnect timer must exist");
      timers.delete(next[0]);
      next[1]();
    },
    timerCount: () => timers.size,
  };
}

function semanticState(snapshot) {
  return {
    messages: snapshot.session.messages,
    tools: snapshot.session.pendingToolCalls,
    plan: snapshot.session.plan,
    usage: snapshot.session.usage,
    commands: snapshot.session.availableCommands,
    mode: snapshot.currentModeId,
    config: snapshot.session.configOptions,
    info: snapshot.sessionInfo,
    terminals: snapshot.terminalStates,
  };
}

function runControllerAssertions(subject, fixture, envelopeStates, controllerCases) {
  const live = controllerHarness(subject);
  live.controller.attach();
  assert.equal(live.sockets.length, 1);
  live.sockets[0].open();
  assert.deepEqual(live.sockets[0].sent[0], { type: "attach", employeeId: "employee-browser" });
  for (const envelope of fixture.live) live.sockets[0].feed(envelope);
  assert.equal(live.controller.snapshot().connection.supportsSteer, true);
  assert.equal(live.controller.snapshot().session.messages[1].parts[0].expanded, false);

  const replay = controllerHarness(subject);
  replay.controller.attach();
  replay.sockets[0].open();
  for (const envelope of fixture.replay) replay.sockets[0].feed(envelope);
  assert.deepEqual(semanticState(replay.controller.snapshot()), semanticState(live.controller.snapshot()));

  const promptResult = live.controller.prompt([{ type: "text", text: "Follow up" }], "queue");
  assert.equal(promptResult.ok, true);
  assert.equal(live.sockets[0].sent.at(-1).deliveryChoice, "normal", "idle sends are normal");
  assert.equal(live.controller.snapshot().session.messages.filter((message) => message.id === "client-1").length, 1);
  live.sockets[0].feed(baseEnvelope(16, "human_echo", {
    clientMessageId: "client-1",
    prompt: { sessionId: "session-browser", prompt: [{ type: "text", text: "Follow up" }] },
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(live.controller.snapshot().session.messages.filter((message) => message.id === "client-1").length, 1);
  live.sockets[0].feed(baseEnvelope(17, "delivery_receipt", {
    clientMessageId: "client-1",
    choice: "normal",
    state: "rejected",
    reason: "Agent unavailable",
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(live.controller.snapshot().optimisticHumans["client-1"].deliveryState, "rejected");
  assert.equal(live.controller.snapshot().session.messages.some((message) => message.id === "client-1"), true);

  const states = controllerHarness(subject);
  states.controller.attach();
  states.sockets[0].open();
  for (const envelope of envelopeStates) {
    states.sockets[0].feed(envelope);
    if (envelope.sequence === 9) {
      assert.deepEqual(states.controller.snapshot().queue.map((item) => item.clientMessageId), ["queue-1", "queue-2"]);
      states.controller.cancelQueued("queue-1");
      assert.deepEqual(states.sockets[0].sent.at(-1), {
        type: "cancel",
        employeeId: "employee-browser",
        queuedClientMessageId: "queue-1",
      });
      assert.equal(states.controller.snapshot().queue.length, 2, "queue remains authoritative until its next snapshot");
    }
    if (envelope.sequence === 16) {
      assert.deepEqual(
        states.controller.snapshot().permissions["permission-browser"].request.request.options.map((option) => [option.optionId, option.kind]),
        [["allow-once", "allow_once"], ["allow-always", "allow_always"], ["reject-once", "reject_once"]],
      );
      assert.equal(states.controller.respondToPermission("permission-browser", "missing-option").ok, false);
      assert.equal(states.controller.respondToPermission("permission-browser", "allow-always").ok, true);
      assert.equal(states.controller.respondToPermission("permission-browser", "allow-once").ok, false);
      assert.deepEqual(states.sockets[0].sent.at(-1), {
        type: "permission_response",
        employeeId: "employee-browser",
        requestId: "permission-browser",
        optionId: "allow-always",
      });
    }
  }
  const statesSnapshot = states.controller.snapshot();
  assert.equal(statesSnapshot.receipts["client-browser"].state, "rejected");
  assert.equal(statesSnapshot.queue.length, 0);
  assert.equal(statesSnapshot.compactions["explicit-boundary"].payload.state, "compacted");
  assert.deepEqual(Object.keys(statesSnapshot.compactions["explicit-boundary"]), ["payload"]);
  assert.equal(statesSnapshot.compactions["automatic-boundary"].payload.state, "failed");
  assert.equal(Object.keys(statesSnapshot.permissions).length, 0);
  assert.equal(statesSnapshot.terminalStates["terminal-browser"].lifecycle, "released");
  assert.equal(statesSnapshot.terminalStates["terminal-browser"].terminalOutput.exitStatus.signal, "TERM");
  assert.equal(Object.keys(statesSnapshot.protocolRejections).length, 1);
  assert.equal(statesSnapshot.connection.supportsSteer, false);
  assert.equal(states.controller.prompt([{ type: "text", text: "Steer" }], "steer").ok, false);
  assert.equal(states.controller.prompt([{ type: "text", text: "Queue" }], "queue").ok, true);
  assert.equal(states.sockets[0].sent.at(-1).deliveryChoice, "queue");
  states.controller.cancelActive();
  assert.deepEqual(states.sockets[0].sent.at(-1), { type: "cancel", employeeId: "employee-browser" });
  states.controller.newConversation();
  assert.deepEqual(states.sockets[0].sent.at(-1), { type: "new_conversation", employeeId: "employee-browser" });

  const gap = controllerHarness(subject);
  gap.controller.attach();
  gap.sockets[0].open();
  gap.sockets[0].feed(fixture.live[0]);
  gap.sockets[0].feed({ ...fixture.live[2], sequence: 3 });
  assert.equal(gap.controller.snapshot().recoverableConnectionError, "Conversation updates were missed. Reconnecting…");
  assert.equal(gap.sockets[0].closed, true);
  assert.equal(gap.timerCount(), 1);
  gap.runTimer();
  gap.sockets[1].open();
  assert.deepEqual(gap.sockets[1].sent[0], {
    type: "attach",
    employeeId: "employee-browser",
    lastSeenBindingGeneration: 1,
    lastSeenSequence: 1,
  });
  gap.sockets[1].feed(baseEnvelope(2, "connection", {
    state: "ready",
    detail: "Recovered",
    supportsSteer: false,
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(gap.controller.snapshot().recoverableConnectionError, null);

  gap.sockets[1].feed(baseEnvelope(3, "protocol_update_rejected", {
    rejectedSessionUpdate: "future_update",
    reason: "Unknown update",
    status: "Agent sent an unsupported update",
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  gap.sockets[1].feed("not json");
  gap.runTimer();
  gap.sockets[2].open();
  gap.sockets[2].feed(baseEnvelope(4, "connection", {
    state: "ready",
    detail: "Recovered again",
    supportsSteer: true,
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(gap.controller.snapshot().recoverableConnectionError, null);
  assert.equal(Object.keys(gap.controller.snapshot().protocolRejections).length, 1);

  const ordering = controllerHarness(subject);
  ordering.controller.attach();
  ordering.sockets[0].open();
  ordering.sockets[0].feed(controllerCases.valid.reset);
  ordering.sockets[0].feed(controllerCases.valid.exactNext);
  const exactSnapshot = ordering.controller.snapshot();
  ordering.sockets[0].feed(controllerCases.rawInvalid.duplicate);
  ordering.sockets[0].feed(controllerCases.rawInvalid.lower);
  assert.deepEqual(ordering.controller.snapshot(), exactSnapshot, "duplicate and lower sequences are ignored");
  ordering.sockets[0].feed(controllerCases.valid.higherGenerationReset);
  assert.equal(ordering.controller.snapshot().cursor.bindingGeneration, 2);
  assert.equal(ordering.controller.snapshot().session.messages.length, 0);
  const higherSnapshot = ordering.controller.snapshot();
  ordering.sockets[0].feed(controllerCases.rawInvalid.staleGeneration);
  assert.deepEqual(ordering.controller.snapshot(), higherSnapshot, "stale generations are ignored");

  const sameBindingReset = controllerHarness(subject);
  sameBindingReset.controller.attach();
  sameBindingReset.sockets[0].open();
  sameBindingReset.sockets[0].feed(controllerCases.valid.reset);
  sameBindingReset.sockets[0].feed(baseEnvelope(2, "protocol_update_rejected", {
    rejectedSessionUpdate: "future_update",
    reason: "Unknown update",
    status: "Agent sent an unsupported update",
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  sameBindingReset.sockets[0].onclose?.();
  sameBindingReset.runTimer();
  sameBindingReset.sockets[1].open();
  sameBindingReset.sockets[1].feed(baseEnvelope(10, "connection", {
    state: "reset",
    detail: "Conversation reloaded",
    supportsSteer: true,
    resetBindingGeneration: 1,
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(sameBindingReset.controller.snapshot().cursor.sequence, 10);
  assert.equal(Object.keys(sameBindingReset.controller.snapshot().protocolRejections).length, 1);
  const afterReplacementReset = sameBindingReset.controller.snapshot();
  sameBindingReset.sockets[1].feed(baseEnvelope(10, "connection", {
    state: "reset",
    detail: "Stale reset",
    supportsSteer: true,
    resetBindingGeneration: 1,
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.deepEqual(
    sameBindingReset.controller.snapshot(),
    afterReplacementReset,
    "equal same-binding resets are ignored",
  );
  sameBindingReset.sockets[1].feed(baseEnvelope(11, "connection", {
    state: "ready",
    detail: "Ready",
    supportsSteer: true,
  }, {
    employeeId: "employee-browser",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
  }));
  assert.equal(sameBindingReset.controller.snapshot().cursor.sequence, 11);

  const replayUnavailable = controllerHarness(subject);
  replayUnavailable.controller.attach();
  replayUnavailable.sockets[0].open();
  replayUnavailable.sockets[0].onclose?.({
    reason: subject.REPLAY_UNAVAILABLE_CLOSE_REASON,
  });
  assert.equal(
    replayUnavailable.controller.snapshot().recoverableConnectionError,
    subject.REPLAY_UNAVAILABLE_ERROR,
  );
  assert.equal(replayUnavailable.timerCount(), 1);

  for (const invalidCase of ["wrongIdentity", "higherGenerationNonReset", "higherGenerationWrongEntityReset"]) {
    const rejected = controllerHarness(subject);
    rejected.controller.attach();
    rejected.sockets[0].open();
    rejected.sockets[0].feed(controllerCases.valid.reset);
    rejected.sockets[0].feed(controllerCases.rawInvalid[invalidCase]);
    assert.equal(rejected.controller.snapshot().recoverableConnectionError, "Conversation data could not be read. Reconnecting…");
    assert.equal(rejected.sockets[0].closed, true);
  }

  const firstEnvelope = controllerHarness(subject);
  firstEnvelope.controller.attach();
  firstEnvelope.sockets[0].open();
  firstEnvelope.sockets[0].feed(controllerCases.valid.exactNext);
  assert.equal(firstEnvelope.controller.snapshot().cursor, null);
  assert.equal(firstEnvelope.controller.snapshot().recoverableConnectionError, "Conversation data could not be read. Reconnecting…");

  const disconnectedPermission = controllerHarness(subject);
  disconnectedPermission.controller.attach();
  disconnectedPermission.sockets[0].open();
  for (const envelope of envelopeStates.slice(0, 16)) disconnectedPermission.sockets[0].feed(envelope);
  assert.equal(Object.keys(disconnectedPermission.controller.snapshot().permissions).length, 1);
  disconnectedPermission.sockets[0].onclose?.();
  assert.equal(Object.keys(disconnectedPermission.controller.snapshot().permissions).length, 0);

  const failedPermission = controllerHarness(subject, { failPermissionSend: true });
  failedPermission.controller.attach();
  failedPermission.sockets[0].open();
  for (const envelope of envelopeStates.slice(0, 16)) failedPermission.sockets[0].feed(envelope);
  assert.equal(failedPermission.controller.respondToPermission("permission-browser", "allow-once").ok, false);
  assert.equal(
    failedPermission.controller.snapshot().permissions["permission-browser"].submittingOptionId,
    null,
    "a local send failure must return the permission prompt to a retryable state",
  );
  assert.equal(failedPermission.controller.respondToPermission("permission-browser", "allow-once").ok, false);

  const humanEchoGrouping = controllerHarness(subject);
  humanEchoGrouping.controller.attach();
  humanEchoGrouping.sockets[0].open();
  for (const envelope of controllerCases.valid.humanEchoGrouping) humanEchoGrouping.sockets[0].feed(envelope);
  const groupedMessages = humanEchoGrouping.controller.snapshot().session.messages.map((message) => ({
    role: message.role,
    text: message.parts.flatMap((part) => part.type === "content" ? part.content : [])
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join(""),
  }));
  assert.deepEqual(groupedMessages, [
    { role: "agent", text: "before" },
    { role: "user", text: "human echo" },
    { role: "agent", text: "after" },
  ], "a non-optimistic human echo is a turn boundary for missing-ID agent chunks");

  const receiptRecency = controllerHarness(subject);
  receiptRecency.controller.attach();
  receiptRecency.sockets[0].open();
  receiptRecency.sockets[0].feed(controllerCases.valid.reset);
  for (const [sequence, clientMessageId, state] of [
    [2, "older-key", "accepted"],
    [3, "newer-key", "accepted"],
    [4, "older-key", "started"],
  ]) {
    receiptRecency.sockets[0].feed(baseEnvelope(sequence, "delivery_receipt", {
      clientMessageId,
      choice: "send_now",
      state,
    }, {
      employeeId: "employee-browser",
      entityId: "ticket-browser",
      acpSessionId: "session-browser",
    }));
  }
  assert.equal(receiptRecency.controller.snapshot().latestReceiptClientMessageId, "older-key");
  assert.equal(receiptRecency.controller.snapshot().latestReceiptSequence, 4);

  gap.sockets[2].onclose?.();
  assert.equal(gap.timerCount(), 1);
  let publicationCount = 0;
  gap.controller.subscribe(() => { publicationCount += 1; });
  const publicationsBeforeDispose = publicationCount;
  gap.controller.dispose();
  assert.equal(gap.timerCount(), 0);
  assert.equal(publicationCount, publicationsBeforeDispose, "dispose must not publish a final snapshot");
  const disposedSnapshot = gap.controller.snapshot();
  gap.sockets[2].feed(fixture.live[1]);
  assert.deepEqual(gap.controller.snapshot(), disposedSnapshot);

  const invalid = subject.validateServerEnvelope({ wireVersion: 1, type: "activity" });
  assert.equal(invalid.ok, false);
  const partialThought = subject.validateServerEnvelope(updateEnvelope(1, {
    sessionUpdate: "agent_thought_chunk",
  }));
  assert.equal(partialThought.ok, false, "partial ACP payloads cannot reach the reducer");

  const receiptEnvelope = (state, extras = {}) => baseEnvelope(1, "delivery_receipt", {
    clientMessageId: "receipt-1",
    choice: "send_now",
    state,
    ...extras,
  });
  for (const invalidReceipt of [
    receiptEnvelope("queued"),
    receiptEnvelope("accepted", { queuePosition: 1 }),
    receiptEnvelope("rejected"),
    receiptEnvelope("started", { reason: "not allowed" }),
  ]) {
    assert.equal(subject.validateServerEnvelope(invalidReceipt).ok, false);
  }
  assert.equal(subject.validateServerEnvelope(receiptEnvelope("queued", { queuePosition: 1 })).ok, true);
  assert.equal(subject.validateServerEnvelope(receiptEnvelope("rejected", { reason: "rejected" })).ok, true);
  assert.equal(subject.validateServerEnvelope(receiptEnvelope("interrupted", { reason: "interrupted" })).ok, true);

  const compactionEnvelope = (state, extras = {}) => baseEnvelope(1, "context_compaction", {
    boundaryId: "boundary-1",
    state,
    trigger: "explicit",
    ...extras,
  });
  assert.equal(subject.validateServerEnvelope(compactionEnvelope("compacting")).ok, true);
  assert.equal(subject.validateServerEnvelope(compactionEnvelope("compacted")).ok, true);
  assert.equal(subject.validateServerEnvelope(compactionEnvelope("failed", { reason: "Exact failure" })).ok, true);
  assert.equal(subject.validateServerEnvelope(compactionEnvelope("failed")).ok, false);
  assert.equal(subject.validateServerEnvelope(compactionEnvelope("compacted", { reason: "not failed" })).ok, false);
  assert.equal(
    subject.validateServerEnvelope(compactionEnvelope("compacted", { summary: "private context" })).ok,
    false,
    "backend compaction context must never enter the public transport",
  );

  for (const mismatchedSequence of [
    baseEnvelope(2, "activity", { state: "working", detail: "Working", sequence: 1 }),
    {
      ...envelopeStates[15],
      sequence: 2,
      payload: {
        ...envelopeStates[15].payload,
        openedSequence: 1,
      },
    },
    {
      ...envelopeStates[16],
      sequence: 2,
      payload: {
        ...envelopeStates[16].payload,
        settledSequence: 1,
      },
    },
  ]) {
    assert.equal(subject.validateServerEnvelope(mismatchedSequence).ok, false);
  }
}

console.log("acp-browser-state.test.mjs: all assertions passed");
