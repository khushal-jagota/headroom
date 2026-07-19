import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

// Compile the pure-TS client (no imports, so no import-replacement needed) — mirrors the
// ws-connection harness.
const source = await readFile(new URL("../src/lib/neutralPane.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-neutral-pane-"));
const modulePath = join(dir, "neutralPane.mjs");
await writeFile(modulePath, compiled, "utf8");
const { createNeutralPaneClient } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

const EMPLOYEE = "agent_panels_chief_of_staff";

// --- fakes -----------------------------------------------------------------------------------

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    this.sent = [];
    this.closed = false;
  }
  send(data) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
  }
  sentRequests() {
    return this.sent.map((raw) => JSON.parse(raw));
  }
}

function makeHarness() {
  const sockets = [];
  const timers = new Map();
  let now = 0;
  let nextTimer = 1;
  const states = [];
  const client = createNeutralPaneClient({
    url: "ws://panels.test/api/relay/neutral",
    employeeEntityId: EMPLOYEE,
    socketFactory: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    now: () => now,
    setTimeout: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, due: now + delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    onState: (snapshot) => states.push(snapshot)
  });
  const advance = (ms) => {
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
  };
  return { client, sockets, states, advance, latest: () => states.at(-1) };
}

function event(kind, extra = {}) {
  return JSON.stringify({ neutral: "event", kind, employee_entity_id: EMPLOYEE, ...extra });
}

function feed(socket, kind, extra = {}) {
  socket.onmessage({ data: event(kind, extra) });
}

// --- attach / re-attach ----------------------------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  assert.equal(sockets.length, 1, "attach opens exactly one socket");
  sockets[0].onopen();
  const attachReq = sockets[0].sentRequests()[0];
  assert.deepEqual(attachReq, {
    neutral: "request",
    kind: "attach_to_employee",
    employee_entity_id: EMPLOYEE
  });

  // A history snapshot renders the transcript.
  feed(sockets[0], "history_snapshot", {
    messages: [
      { role: "human", text: "hi", tool_name: null },
      { role: "assistant", text: "hello", tool_name: null }
    ]
  });
  let snap = client.snapshot();
  assert.deepEqual(
    snap.transcript.map((entry) => [entry.role, entry.text]),
    [
      ["human", "hi"],
      ["assistant", "hello"]
    ]
  );

  // Socket drop marks disconnected and schedules a reconnect.
  sockets[0].onclose();
  assert.equal(client.snapshot().connected, false);
}

// Reconnect + re-attach across a drop replaces the transcript (recovery).
{
  const { client, sockets, advance } = makeHarness();
  client.attach();
  sockets[0].onopen();
  feed(sockets[0], "history_snapshot", {
    messages: [{ role: "assistant", text: "first", tool_name: null }]
  });
  sockets[0].onclose();
  advance(500);
  assert.equal(sockets.length, 2, "a reconnect opened a second socket");
  sockets[1].onopen();
  assert.deepEqual(sockets[1].sentRequests()[0].kind, "attach_to_employee");
  feed(sockets[1], "history_snapshot", {
    messages: [{ role: "assistant", text: "second", tool_name: null }]
  });
  assert.deepEqual(
    client.snapshot().transcript.map((entry) => entry.text),
    ["second"],
    "the fresh snapshot REPLACES the transcript"
  );
}

// child_reset clears the in-flight turn and re-attaches on the same socket.
{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  feed(sockets[0], "history_snapshot", { messages: [] });
  feed(sockets[0], "turn_started");
  feed(sockets[0], "assistant_text_delta", { text: "partial" });
  assert.equal(client.snapshot().streamingText, "partial");
  const beforeReset = sockets[0].sent.length;
  feed(sockets[0], "child_reset");
  assert.equal(client.snapshot().streamingText, "", "child_reset cleared the in-flight turn");
  assert.equal(client.snapshot().turnActive, false);
  assert.ok(sockets[0].sent.length > beforeReset, "child_reset re-sent attach");
  assert.equal(sockets[0].sentRequests().at(-1).kind, "attach_to_employee");
}

// --- the EXHAUSTIVE 13-kind fold-in reducer --------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });

  // turn_started + three deltas accumulate token-by-token.
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "he" });
  feed(socket, "assistant_text_delta", { text: "ll" });
  feed(socket, "assistant_text_delta", { text: "o" });
  assert.equal(client.snapshot().streamingText, "hello");
  assert.equal(client.snapshot().turnActive, true);

  // thinking accumulates separately.
  feed(socket, "thinking_delta", { text: "think1" });
  feed(socket, "thinking_delta", { text: "think2" });
  assert.equal(client.snapshot().thinkingText, "think1think2");

  // tool_activity upserts by (tool_id, phase).
  feed(socket, "tool_activity", {
    tool_id: "t1",
    tool_name: "grep",
    phase: "started",
    preview: "a"
  });
  feed(socket, "tool_activity", {
    tool_id: "t1",
    tool_name: "grep",
    phase: "started",
    preview: "b"
  });
  assert.equal(client.snapshot().toolActivity.length, 1, "same (id,phase) upserts");
  assert.equal(client.snapshot().toolActivity[0].preview, "b");
  feed(socket, "tool_activity", {
    tool_id: "t1",
    tool_name: "grep",
    phase: "completed",
    preview: "done"
  });
  assert.equal(client.snapshot().toolActivity.length, 2, "a new phase adds a row");

  // agent_question + tool_approval_request set the pending affordances.
  feed(socket, "agent_question", {
    request_id: "q1",
    prompt_text: "pick",
    choices: ["a", "b"]
  });
  assert.deepEqual(client.snapshot().pendingQuestion, {
    requestId: "q1",
    promptText: "pick",
    choices: ["a", "b"]
  });
  feed(socket, "tool_approval_request", { request_id: "ap1", summary: "rm -rf" });
  assert.deepEqual(client.snapshot().pendingApproval, { requestId: "ap1", summary: "rm -rf" });

  // session_titled sets the title.
  feed(socket, "session_titled", { title: "My chat" });
  assert.equal(client.snapshot().title, "My chat");

  // catalog_result parses the NATIVE commands.catalog payload verbatim (top-level pairs +
  // skill_count + categories[].pairs — the shape shared_gateway._build_catalog reads; the
  // client stores it as-is and the pane extracts skills from pairs[-skill_count:]).
  const nativeCatalog = {
    pairs: [
      ["/help", "Show help"],
      ["$summarize", "Summarize the conversation so far."]
    ],
    skill_count: 1,
    categories: [{ name: "Skills", pairs: [["$summarize", "Summarize the conversation so far."]] }]
  };
  feed(socket, "catalog_result", { payload_json: JSON.stringify(nativeCatalog) });
  assert.deepEqual(client.snapshot().catalogPayload, nativeCatalog);

  // passthrough (internal/telemetry) is DROPPED from the transcript, not shown as "[kind]".
  const beforePass = client.snapshot().transcript.length;
  feed(socket, "passthrough", { native_type: "session.info", payload_json: "{}" });
  assert.equal(client.snapshot().transcript.length, beforePass);

  // a status event sets the activity label; a "compacting" status raises the affordance.
  feed(socket, "status", { status_kind: "compacting", text: "Summarizing…" });
  assert.equal(client.snapshot().statusLabel, "Summarizing…");
  assert.equal(client.snapshot().compacting, true);
  // the compacted event clears the affordance and drops a divider into the history.
  feed(socket, "compacted", {});
  assert.equal(client.snapshot().compacting, false);
  assert.equal(client.snapshot().transcript.at(-1).role, "divider");

  // turn_completed closes the open turn with final_text and CLEARS all ephemeral state,
  // INCLUDING the pending question + approval (F9).
  feed(socket, "turn_completed", { final_text: "hello" });
  const afterComplete = client.snapshot();
  assert.equal(afterComplete.transcript.at(-1).text, "hello");
  assert.equal(afterComplete.transcript.at(-1).role, "assistant");
  assert.equal(afterComplete.streamingText, "");
  assert.equal(afterComplete.thinkingText, "");
  assert.equal(afterComplete.toolActivity.length, 0);
  assert.equal(afterComplete.pendingQuestion, null, "completion clears the pending question");
  assert.equal(afterComplete.pendingApproval, null, "completion clears the pending approval");
  assert.equal(afterComplete.turnActive, false);
}

// turn_failed on an OPEN turn closes it; an IDLE turn_failed (no open turn — compact's 4009)
// appends a standalone failure line without corrupting state (F9).
{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });

  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "partial" });
  feed(socket, "agent_question", { request_id: "q", prompt_text: "?", choices: [] });
  feed(socket, "turn_failed", { reason: "agent_error", detail: "boom" });
  let snap = client.snapshot();
  assert.equal(snap.transcript.at(-1).text, "boom");
  assert.equal(snap.pendingQuestion, null, "turn_failed clears the pending question");
  assert.equal(snap.turnActive, false);
  assert.equal(snap.streamingText, "");

  // Idle failure: no open turn.
  feed(socket, "turn_failed", { reason: "busy_already_running", detail: "busy" });
  snap = client.snapshot();
  assert.equal(snap.transcript.at(-1).role, "system");
  assert.equal(snap.transcript.at(-1).text, "busy");
  assert.equal(snap.turnActive, false);
}

// history_snapshot REPLACES the transcript AND clears ALL ephemeral state incl. optimistic line.
{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "midstream" });
  feed(socket, "thinking_delta", { text: "hmm" });
  feed(socket, "agent_question", { request_id: "q", prompt_text: "?", choices: [] });
  client.send("optimistic", []);
  feed(socket, "history_snapshot", {
    messages: [{ role: "assistant", text: "recovered", tool_name: null }]
  });
  const snap = client.snapshot();
  assert.deepEqual(
    snap.transcript.map((entry) => entry.text),
    ["recovered"],
    "history_snapshot replaced everything including the optimistic line"
  );
  assert.equal(snap.streamingText, "");
  assert.equal(snap.thinkingText, "");
  assert.equal(snap.pendingQuestion, null);
}

// --- optimistic human message (F8) -----------------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  feed(sockets[0], "history_snapshot", { messages: [] });
  client.send("hi", []);
  const snap = client.snapshot();
  assert.deepEqual(snap.transcript.at(-1), { role: "human", text: "hi", images: undefined });
  // A following snapshot replaces it (no duplicate).
  feed(sockets[0], "history_snapshot", {
    messages: [
      { role: "human", text: "hi", tool_name: null },
      { role: "assistant", text: "echo: hi", tool_name: null }
    ]
  });
  assert.equal(
    client.snapshot().transcript.filter((entry) => entry.text === "hi").length,
    1,
    "no duplicate human line after the snapshot"
  );
}

// --- request emission --------------------------------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  socket.sent.length = 0; // drop the attach request

  client.send("hi", ["ref1"]);
  assert.deepEqual(socket.sentRequests().at(-1), {
    neutral: "request",
    kind: "send_message",
    employee_entity_id: EMPLOYEE,
    text: "hi",
    image_refs: ["ref1"]
  });

  client.answer("q1", "yes");
  assert.deepEqual(socket.sentRequests().at(-1), {
    neutral: "request",
    kind: "answer_question",
    employee_entity_id: EMPLOYEE,
    request_id: "q1",
    answer: "yes"
  });

  client.respondApproval("ap1", "approve", true);
  assert.deepEqual(socket.sentRequests().at(-1), {
    neutral: "request",
    kind: "respond_to_approval",
    employee_entity_id: EMPLOYEE,
    request_id: "ap1",
    decision: "approve",
    apply_to_all: true
  });

  client.interrupt();
  assert.equal(socket.sentRequests().at(-1).kind, "interrupt");
  client.compact();
  assert.equal(socket.sentRequests().at(-1).kind, "compact");
  client.listCatalog();
  assert.equal(socket.sentRequests().at(-1).kind, "list_catalog");
  client.newConversation();
  assert.equal(socket.sentRequests().at(-1).kind, "new_conversation");
}

// A mid-turn send emits the same send request with NO busy gating (composer never disabled).
{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "streaming" });
  socket.sent.length = 0;
  client.send("mid", []);
  assert.equal(socket.sentRequests().at(-1).kind, "send_message", "mid-turn send is accepted");
  assert.equal(socket.sentRequests().at(-1).text, "mid");
}

// --- readiness barrier (historyLoaded) -------------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  // A socket open alone is NOT ready: no history snapshot has landed yet.
  assert.equal(client.snapshot().historyLoaded, false, "not ready before the first snapshot");
  feed(sockets[0], "history_snapshot", { messages: [] });
  assert.equal(
    client.snapshot().historyLoaded,
    true,
    "ready only after the FIRST history snapshot"
  );
}

// --- newConversation clears ALL local state on send (MINOR 1) --------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", {
    messages: [{ role: "assistant", text: "old", tool_name: null }]
  });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "midstream" });
  feed(socket, "thinking_delta", { text: "hmm" });
  feed(socket, "agent_question", { request_id: "q", prompt_text: "?", choices: [] });
  client.send("optimistic", []);
  socket.sent.length = 0;

  client.newConversation();
  const snap = client.snapshot();
  // The transcript (incl. the settled "old" line + the optimistic human line), in-flight text,
  // thinking, and the pending question are ALL cleared locally; the incoming empty snapshot
  // rebuilds authoritatively.
  assert.deepEqual(snap.transcript, [], "new-conversation cleared the transcript");
  assert.equal(snap.streamingText, "");
  assert.equal(snap.thinkingText, "");
  assert.equal(snap.pendingQuestion, null);
  assert.equal(socket.sentRequests().at(-1).kind, "new_conversation");
}

// --- capability signal (capabilities.ts) ------------------------------------------------------
// Folded in here to stay within the plan's §9 test-file allowlist (neutral-pane.test.mjs). The
// capability store gates which Chief pane mounts; only an explicit boolean is a valid answer.
{
  const capsSource = await readFile(
    new URL("../src/lib/capabilities.ts", import.meta.url),
    "utf8"
  );

  let fetchResult = null;
  let fetchError = null;
  const fetchCalls = [];
  let current = "unknown";

  globalThis.__capsTestDeps = {
    fetchJson: async (path) => {
      fetchCalls.push(path);
      if (fetchError) throw fetchError;
      return fetchResult;
    },
    writable: (initial) => {
      current = initial;
      return {
        set(value) {
          current = value;
        },
        subscribe(run) {
          run(current);
          return () => undefined;
        }
      };
    }
  };

  const capsExecutable = capsSource
    .replace(
      'import { fetchJson } from "./api";',
      "const { fetchJson } = globalThis.__capsTestDeps;"
    )
    .replace(
      'import { writable } from "svelte/store";',
      "const { writable } = globalThis.__capsTestDeps;"
    );
  const capsCompiled = ts.transpileModule(capsExecutable, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: true
    }
  }).outputText;
  const capsDir = await mkdtemp(join(tmpdir(), "planner-capabilities-"));
  const capsModulePath = join(capsDir, "capabilities.mjs");
  await writeFile(capsModulePath, capsCompiled, "utf8");
  const { resolveRelayChiefFromMeta, markRelayChiefMetaError, retryRelayChiefMeta } =
    await import(capsModulePath);
  await rm(capsDir, { recursive: true, force: true });

  // ONLY an explicit boolean decides.
  resolveRelayChiefFromMeta({ relay_chief_enabled: true });
  assert.equal(current, "enabled", "true -> enabled");
  resolveRelayChiefFromMeta({ relay_chief_enabled: false });
  assert.equal(current, "disabled", "false -> disabled");
  // A successful response MISSING the key must NOT resolve to disabled (a shape drift would
  // otherwise mount the legacy pane) — it resolves to "error".
  resolveRelayChiefFromMeta({});
  assert.equal(current, "error", "missing key -> error, never disabled");
  resolveRelayChiefFromMeta({ relay_chief_enabled: "yes" });
  assert.equal(current, "error", "non-boolean -> error");
  resolveRelayChiefFromMeta({ relay_chief_enabled: 1 });
  assert.equal(current, "error", "truthy-non-boolean -> error (not enabled)");

  markRelayChiefMetaError();
  assert.equal(current, "error");

  // Retry re-fetches /api/meta and re-resolves.
  fetchCalls.length = 0;
  fetchError = null;
  fetchResult = { relay_chief_enabled: true };
  await retryRelayChiefMeta();
  assert.deepEqual(fetchCalls, ["/api/meta"], "retry re-fetches /api/meta");
  assert.equal(current, "enabled", "retry success -> enabled");

  fetchError = new Error("network");
  await retryRelayChiefMeta();
  assert.equal(current, "error", "retry failure -> error");

  fetchError = null;
  fetchResult = {};
  await retryRelayChiefMeta();
  assert.equal(current, "error", "retry with missing key -> error");
}

console.log("neutral-pane.test.mjs: all assertions passed");
