import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

// Wave 5 (S3): the ticket chat rail folds a neutral pane keyed to the TICKET entity id. This
// mirrors neutral-pane.test.mjs but drives the same framework-free client with a ticket entity
// id, proving the pane the route mounts is entity-parametric — the exact property
// TicketRoute.svelte relies on when it mounts <ChiefNeutralPane entityId={stableId} ...>. If the
// client hard-coded the Chief employee id, these attach/send/fold-in assertions would fail.

const source = await readFile(new URL("../src/lib/neutralPane.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-ticket-neutral-pane-"));
const modulePath = join(dir, "neutralPane.mjs");
await writeFile(modulePath, compiled, "utf8");
const { createNeutralPaneClient } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

// A ticket entity id (the shape TicketRoute passes as stableId) — NOT the Chief id.
const TICKET_ENTITY = "t_ticket_pane_demo";

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
    employeeEntityId: TICKET_ENTITY,
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
  return JSON.stringify({ neutral: "event", kind, employee_entity_id: TICKET_ENTITY, ...extra });
}

function feed(socket, kind, extra = {}) {
  socket.onmessage({ data: event(kind, extra) });
}

// --- attach carries the TICKET entity id -----------------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  assert.equal(sockets.length, 1, "attach opens exactly one socket");
  sockets[0].onopen();
  assert.deepEqual(
    sockets[0].sentRequests()[0],
    {
      neutral: "request",
      kind: "attach_to_employee",
      employee_entity_id: TICKET_ENTITY
    },
    "the ticket pane attaches to the TICKET employee id, not the Chief"
  );
}

// --- a step turn's frames stream token-by-token into the ticket pane -------------------------
// A worker step submitted through the pool child emits the SAME native turn frames the relay
// fans out to every subscriber of that ticket employee. The pane folds them in exactly like a
// human turn — so a step turn streams live into the ticket pane for free.

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });

  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "wor" });
  assert.equal(client.snapshot().streamingText, "wor", "partial step text renders");
  assert.equal(client.snapshot().turnActive, true);
  feed(socket, "assistant_text_delta", { text: "king" });
  assert.equal(client.snapshot().streamingText, "working");

  // Settle on completed: the settled turn shows in the transcript, streaming clears.
  feed(socket, "turn_completed", { final_text: "working" });
  const done = client.snapshot();
  assert.equal(done.transcript.at(-1).text, "working");
  assert.equal(done.transcript.at(-1).role, "assistant");
  assert.equal(done.streamingText, "");
  assert.equal(done.turnActive, false);
}

// --- settlement on failed renders a failure line --------------------------------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "partial" });
  feed(socket, "turn_failed", { reason: "agent_error", detail: "step failed" });
  const snap = client.snapshot();
  assert.equal(snap.transcript.at(-1).text, "step failed", "a failed step renders its detail");
  assert.equal(snap.turnActive, false);
  assert.equal(snap.streamingText, "");
}

// --- a human send on the ticket pane carries the ticket id, never busy-gated -----------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  socket.sent.length = 0;

  client.send("guide the ticket step", []);
  assert.deepEqual(socket.sentRequests().at(-1), {
    neutral: "request",
    kind: "send_message",
    employee_entity_id: TICKET_ENTITY,
    text: "guide the ticket step",
    image_refs: []
  });
  // Optimistic human line appears immediately.
  assert.deepEqual(client.snapshot().transcript.at(-1), {
    role: "human",
    text: "guide the ticket step",
    images: undefined
  });
}

// A human send DURING a running step is accepted with no busy gating (native queue semantics).
{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "mid step stream" });
  socket.sent.length = 0;
  client.send("interject", []);
  assert.equal(
    socket.sentRequests().at(-1).kind,
    "send_message",
    "a mid-step human send is accepted (never disabled)"
  );
  assert.equal(socket.sentRequests().at(-1).text, "interject");
}

// --- child reset re-attaches to the TICKET employee and restores history --------------------

{
  const { client, sockets } = makeHarness();
  client.attach();
  sockets[0].onopen();
  const socket = sockets[0];
  feed(socket, "history_snapshot", { messages: [] });
  feed(socket, "turn_started");
  feed(socket, "assistant_text_delta", { text: "streaming step" });
  assert.equal(client.snapshot().streamingText, "streaming step");
  const before = socket.sent.length;
  feed(socket, "child_reset");
  assert.equal(client.snapshot().streamingText, "", "child_reset cleared the in-flight step turn");
  assert.equal(client.snapshot().turnActive, false);
  const reattach = socket.sentRequests().at(-1);
  assert.equal(reattach.kind, "attach_to_employee", "child_reset re-attached");
  assert.equal(reattach.employee_entity_id, TICKET_ENTITY, "re-attach carries the ticket id");
  assert.ok(socket.sent.length > before);
  // The fresh history snapshot restores the durable transcript.
  feed(socket, "history_snapshot", {
    messages: [{ role: "assistant", text: "restored step output", tool_name: null }]
  });
  assert.deepEqual(
    client.snapshot().transcript.map((entry) => entry.text),
    ["restored step output"],
    "history restored after re-attach"
  );
}

console.log("ticket-neutral-pane.test.mjs: all assertions passed");
