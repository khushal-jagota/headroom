import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp-conformance-"));
const entryPath = join(webRoot, "tests", `.acp-conformance-subject-${process.pid}.ts`);
const evidencePath = join(temporaryDirectory, "evidence.json");
const fixture = JSON.parse(
  await readFile(join(repositoryRoot, "tests", "fixtures", "acp", "browser-live-replay-v1.json"), "utf8"),
);

class FakeSocket {
  constructor() {
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
  }
  send() {}
  close() {}
  open() { this.onopen?.(); }
  feed(envelope) { this.onmessage?.({ data: JSON.stringify(envelope) }); }
}

try {
  await writeFile(entryPath, [
    "export * from '../src/lib/acp/panelsTransport';",
    "export * from '../src/lib/acp/conversationState';",
    "export * from '../src/lib/acp/conversationController';",
  ].join("\n"));
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

  const createHarness = () => {
    let socket;
    const planSnapshots = [];
    const controller = subject.createConversationController({
      employeeId: "employee-browser",
      transportFactory: (callbacks) => subject.createPanelsTransport({
        url: "ws://panels.test/acp",
        socketFactory: () => (socket = new FakeSocket()),
        ...callbacks,
      }),
      reconnectDelayMs: 10,
      setTimer: () => 1,
      clearTimer: () => undefined,
      now: () => 1000,
      fallbackId: (turn, role, segment) => `fallback-${turn}-${role}-${segment}`,
      clientMessageId: () => "client-conformance",
    });
    controller.subscribe((snapshot) => {
      planSnapshots.push(snapshot.session.plan.map((entry) => entry.content));
    });
    controller.attach();
    socket.open();
    return {
      controller,
      planSnapshots,
      feed(envelope) { socket.feed(envelope); },
      snapshot() { return controller.snapshot(); },
    };
  };
  const drive = (stream) => {
    const harness = createHarness();
    for (const envelope of stream) harness.feed(envelope);
    return harness;
  };

  const live = drive(fixture.live);
  const replay = drive(fixture.replay);
  const thoughtChunks = (snapshot) => snapshot.session.messages
    .flatMap((message) => message.parts)
    .filter((part) => part.type === "thought")
    .flatMap((part) => part.thought)
    .filter((block) => block.type === "text")
    .map((block) => block.text);
  const assistantOutputs = (snapshot) => snapshot.session.messages
    .filter((message) => message.role === "agent")
    .flatMap((message) => message.parts)
    .filter((part) => part.type === "content")
    .flatMap((part) => part.content)
    .filter((block) => block.type === "text")
    .map((block) => block.text);
  const missingGroups = (snapshot) => snapshot.session.messages
    .filter((message) => message.role === "agent")
    .filter((message) => message.parts.some((part) => part.type === "thought" || part.type === "content"))
    .map((message) => message.id);

  const envelope = (sequence, update) => ({
    wireVersion: 1,
    employeeId: "employee-browser",
    entityKind: "ticket",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
    bindingGeneration: 1,
    sequence,
    type: "acp_session_update",
    payload: { sessionId: "session-browser", update },
  });
  const reset = {
    wireVersion: 1,
    employeeId: "employee-browser",
    entityKind: "ticket",
    entityId: "ticket-browser",
    acpSessionId: "session-browser",
    bindingGeneration: 1,
    sequence: 1,
    type: "connection",
    payload: { state: "reset", detail: "Reset", supportsSteer: true, resetBindingGeneration: 1 },
  };
  const grouping = createHarness();
  grouping.feed(reset);
  const stableGroupIds = [];
  grouping.feed(envelope(2, {
    sessionUpdate: "agent_message_chunk",
    messageId: "stable-id",
    content: { type: "text", text: "a" },
  }));
  stableGroupIds.push(grouping.snapshot().session.messages.find((message) => message.id === "stable-id").id);
  grouping.feed(envelope(3, {
    sessionUpdate: "agent_message_chunk",
    messageId: "stable-id",
    content: { type: "text", text: "b" },
  }));
  stableGroupIds.push(grouping.snapshot().session.messages.find((message) => message.id === "stable-id").id);

  const boundaryResetGroupIds = [];
  grouping.feed(envelope(4, {
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: "first missing-ID group" },
  }));
  boundaryResetGroupIds.push(grouping.snapshot().session.messages.at(-1).id);
  grouping.feed(envelope(5, {
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: " continues" },
  }));
  assert.equal(grouping.snapshot().session.messages.at(-1).id, boundaryResetGroupIds[0]);
  grouping.feed(envelope(6, {
    sessionUpdate: "tool_call",
    toolCallId: "boundary-tool",
    title: "Boundary",
    status: "completed",
  }));
  grouping.feed(envelope(7, {
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: "second missing-ID group" },
  }));
  boundaryResetGroupIds.push(grouping.snapshot().session.messages.at(-1).id);
  grouping.feed(envelope(8, {
    sessionUpdate: "plan",
    entries: [{ content: "Boundary", status: "pending", priority: "medium" }],
  }));
  grouping.feed(envelope(9, {
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: "third missing-ID group" },
  }));
  boundaryResetGroupIds.push(grouping.snapshot().session.messages.at(-1).id);
  assert.equal(boundaryResetGroupIds.length, 3, "boundary uniqueness evidence must be non-vacuous");

  const rejectedDiscriminators = ["future_update", "agent_thought_chunk"];
  const rejection = createHarness();
  rejection.feed(reset);
  rejectedDiscriminators.forEach((discriminator, index) => rejection.feed({
    ...reset,
    sequence: index + 2,
    type: "protocol_update_rejected",
    payload: {
      rejectedSessionUpdate: discriminator,
      reason: "Rejected by the frozen server validator",
      status: "Agent sent an unsupported update",
    },
  }));

  const liveSnapshot = live.snapshot();
  const replaySnapshot = replay.snapshot();
  const evidence = {
    typedThought: {
      liveUpdateTypes: fixture.live.filter((item) => item.type === "acp_session_update").map((item) => item.payload.update.sessionUpdate),
      replayUpdateTypes: fixture.replay.filter((item) => item.type === "acp_session_update").map((item) => item.payload.update.sessionUpdate),
      liveThoughtChunks: thoughtChunks(liveSnapshot),
      replayThoughtChunks: thoughtChunks(replaySnapshot),
      liveAssistantOutputs: assistantOutputs(liveSnapshot),
      replayAssistantOutputs: assistantOutputs(replaySnapshot),
    },
    messageGrouping: {
      stableGroupIds,
      missingIdLiveGroupIds: missingGroups(liveSnapshot),
      missingIdReplayGroupIds: missingGroups(replaySnapshot),
      boundaryResetGroupIds,
    },
    planToolReconciliation: {
      observedPlanSnapshots: live.planSnapshots.filter((snapshot, index, all) => (
        index === 0 || JSON.stringify(snapshot) !== JSON.stringify(all[index - 1])
      )).filter((snapshot) => snapshot.length),
      finalPlan: liveSnapshot.session.plan.map((entry) => entry.content),
      toolCallIds: Object.keys(liveSnapshot.session.pendingToolCalls),
      finalToolStatus: liveSnapshot.session.pendingToolCalls["tool-1"].status,
    },
    protocolRejection: {
      rejectedDiscriminators,
      visibleStatuses: Object.values(rejection.snapshot().protocolRejections).map((item) => item.status),
      assistantTextFallbacks: assistantOutputs(rejection.snapshot()),
    },
  };
  assert.equal(new Set(evidence.messageGrouping.boundaryResetGroupIds).size, 3);
  await writeFile(evidencePath, JSON.stringify(evidence, null, 2));
  const output = execFileSync(
    join(repositoryRoot, ".venv", "bin", "python"),
    [join(repositoryRoot, "tests", "support", "acp_browser_conformance.py"), evidencePath],
    { cwd: repositoryRoot, encoding: "utf8" },
  );
  assert.match(output, /probes 2, 3, 4, and 10 passed; mutations failed/);
} finally {
  await rm(entryPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

console.log("acp-browser-conformance.test.mjs: all assertions passed");
