/** Public production smoke; the caller owns the populated seed, server and restart. */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import type { public_event } from "../../server/conversation/http/record.ts";

type Json = Record<string, unknown>;
type Event = ReturnType<typeof public_event>;
type Step = (name: string, evidence?: unknown) => void;
export interface SmokeOptions {
  model: string;
  reply: string;
  workspace: string;
  step?: Step;
}
export interface SmokeState {
  ticket_id: string;
  conversation_id: string;
  goal_id: string;
  stored_file_id: string;
  events: Event[];
  notification_settings: Json;
  model: string;
}
const execute = promisify(execFile);
const attachment = "Private production smoke attachment.\n";
const title = "Production smoke Ticket";
const proposal = "Production smoke proposal ready for owner review.";
const log: Step = (name, evidence) =>
  console.info(name, JSON.stringify(evidence ?? {}));
function text(value: unknown): string {
  assert.equal(typeof value, "string");
  return value as string;
}
function client(base: string) {
  const url = new URL(base);
  assert.equal(url.protocol, "http:");
  assert.equal(url.hostname, "127.0.0.1");
  assert.ok(url.port && url.port !== "0");
  assert.equal(url.username + url.password + url.search + url.hash, "");
  assert.equal(url.pathname, "/");
  return async <T = Json>(
    path: string,
    body?: unknown,
    headers: Json = {},
  ): Promise<T> => {
    const response = await fetch(url.origin + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "content-type": "application/json", ...headers } as Record<
        string,
        string
      >,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    const output = await response.text();
    assert.ok(response.ok, `${path}: ${response.status}: ${output}`);
    return JSON.parse(output) as T;
  };
}
async function cli_ticket(
  base: string,
  id: string,
  environment: NodeJS.ProcessEnv,
): Promise<Json> {
  const { stdout } = await execute(
    process.execPath,
    [
      fileURLToPath(new URL("../../server/cli/main.ts", import.meta.url)),
      "ticket",
      "show",
      id,
      "--json",
    ],
    {
      env: {
        ...environment,
        PLAN_SERVER_URL: new URL(base).origin,
        PLAN_ACTOR: "",
        PLAN_TICKET_ID: "",
        PLAN_MANAGER_ID: "",
        PLAN_CONVERSATION_ID: "",
      },
      timeout: 15_000,
      killSignal: "SIGKILL",
      maxBuffer: 1024 * 1024,
    },
  );
  const ticket = JSON.parse(stdout) as Json;
  assert.equal(ticket.id, id);
  assert.equal(ticket.title, title);
  return ticket;
}
async function poll<T>(
  read: () => Promise<T>,
  accepted: (value: T) => boolean,
): Promise<T> {
  const deadline = performance.now() + 15_000;
  while (true) {
    const value = await read();
    if (accepted(value)) return value;
    assert.ok(
      performance.now() < deadline,
      "Production smoke observation timed out",
    );
    await delay(100);
  }
}
async function review_gate(base: string, ticket_id: string): Promise<void> {
  const api = client(base);
  const review = await api<{ items: Json[] }>("/api/review");
  assert.ok(
    review.items.some(
      (item) =>
        item.ticket_id === ticket_id && item.review_item_type === "proposal",
    ),
  );
  await poll(
    () =>
      api<{ rows: Json[] }>(
        `/api/attention/summary?group_by=ticket&scope_kind=ticket&scope_id=${ticket_id}`,
      ),
    (attention) => attention.rows.some((row) => row.id === ticket_id),
  );
}
async function download(
  base: string,
  conversation: string,
  file: string,
): Promise<void> {
  client(base);
  const response = await fetch(
    `${new URL(base).origin}/api/conversation/conversations/${conversation}/files/${file}`,
    { redirect: "error", signal: AbortSignal.timeout(15_000) },
  );
  assert.equal(response.status, 200);
  assert.ok(response.headers.get("content-type")?.startsWith("text/plain"));
  assert.equal(await response.text(), attachment);
}
export async function smoke_before_restart(
  base: string,
  environment: NodeJS.ProcessEnv,
  options: SmokeOptions,
): Promise<SmokeState> {
  const api = client(base),
    step = options.step ?? log;
  const populated = await api<{ tickets: Json[] }>("/api/tickets?limit=5");
  assert.ok(
    populated.tickets.length > 0,
    "Expected the existing populated synthetic seed",
  );
  step("01 populated production reads", { tickets: populated.tickets.length });
  const ticket = await api("/api/tickets", {
    title,
    worker_type: "coding",
    locale: "vps",
  });
  const ticket_id = text(ticket.id);
  step("02 create Ticket through HTTP", { ticket_id });
  await cli_ticket(base, ticket_id, environment);
  step("03 read Ticket through executed TypeScript CLI");
  const proposed = await api(
    `/api/tickets/${ticket_id}/propose`,
    { body: proposal },
    {
      "x-plan-actor": "worker",
      "x-plan-ticket-id": ticket_id,
    },
  );
  assert.equal(proposed.ticket_status, "awaiting_approval");
  step("04 proposal opens real owner review gate");
  await review_gate(base, ticket_id);
  step("05 read review notification and attention");

  const conversation_id = "smoke-" + randomUUID();
  const conversation = `/api/conversation/conversations/${conversation_id}`;
  const started = await api("/api/conversation/conversations", {
    conversation_id,
    backend_key: "codex",
    model: options.model,
    workspace_folder: options.workspace,
  });
  assert.equal(started.model, options.model);
  step("06 create manual conversation through production adapter", {
    conversation_id,
  });
  const sent = await api(conversation + "/send", {
    content: [{ piece: "text", text: "Production smoke prompt." }],
    sender_label: "Owner",
    sender_message_id: "smoke-text",
  });
  assert.equal(sent.fate, "started");
  async function completed(replies: number): Promise<Event[]> {
    return poll(
      async () => {
        const events = await api<{ events: Event[] }>(conversation + "/events");
        const state = await api(conversation);
        return { events: events.events, running: state.is_running };
      },
      (value) =>
        value.running === false &&
        value.events.filter(
          (event) =>
            event.kind === "agent_message" &&
            event.payload.text === options.reply,
        ).length === replies,
    ).then((value) => value.events);
  }
  await completed(1);
  step("07 actual adapter reply recorded and turn settled");
  const upload = await api(conversation + "/send", {
    content: [
      {
        piece: "file",
        file_name: "smoke.txt",
        media_type: "text/plain",
        data: Buffer.from(attachment).toString("base64"),
      },
    ],
    sender_label: "Owner",
    sender_message_id: "smoke-upload",
  });
  assert.equal(upload.fate, "started");
  step("08 upload text file through ordinary send");
  const events = await completed(2);
  const uploaded = events.find(
    (event) =>
      event.kind === "prompt" &&
      event.payload.sender_message_id === "smoke-upload",
  );
  assert.ok(uploaded);
  const pieces = uploaded.payload.content as Json[];
  const file = pieces.find(
    (piece) => piece.piece === "file" && piece.file_name === "smoke.txt",
  );
  assert.ok(file);
  const stored_file_id = text(file.stored_file_id);
  await download(base, conversation_id, stored_file_id);
  step("09 second reply settles and uploaded bytes round trip", {
    stored_file_id,
  });
  const goal = await api("/api/goals", {
    title: "Production smoke goal",
    project_id: "project_other",
    kind: "binary",
    primary_checklist_block_id: null,
  });
  const goal_id = text(goal.id);
  step("10 create binary goal", { goal_id });
  const response = await fetch(`${new URL(base).origin}/api/goals/${goal_id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ completed: true }),
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
  });
  assert.equal(response.status, 200);
  assert.equal(((await response.json()) as Json).completed, true);
  step("11 complete goal");
  const notification_settings = await api("/api/notifications/settings");
  assert.ok(Array.isArray(notification_settings.subjects));
  step("12 read notification settings without subscribing or push");
  return {
    ticket_id,
    conversation_id,
    goal_id,
    stored_file_id,
    events,
    notification_settings,
    model: options.model,
  };
}
export async function smoke_after_restart(
  base: string,
  state: SmokeState,
  environment: NodeJS.ProcessEnv,
  step: Step = log,
): Promise<void> {
  const api = client(base);
  const conversation = `/api/conversation/conversations/${state.conversation_id}`;
  const persisted = await api(conversation);
  assert.equal(persisted.model, state.model);
  assert.equal(persisted.is_running, false);
  const replay = await api<{ events: Event[] }>(conversation + "/events");
  assert.deepEqual(replay.events.slice(0, state.events.length), state.events);
  step("13 restart preserves complete conversation and replies");
  await download(base, state.conversation_id, state.stored_file_id);
  assert.equal((await api(`/api/goals/${state.goal_id}`)).completed, true);
  step("14 restart preserves uploaded bytes and completed goal");
  const ticket = await cli_ticket(base, state.ticket_id, environment);
  assert.equal(ticket.ticket_status, "awaiting_approval");
  await review_gate(base, state.ticket_id);
  assert.deepEqual(
    await api("/api/notifications/settings"),
    state.notification_settings,
  );
  step("15 CLI, owner review notification and attention survive restart");
}
