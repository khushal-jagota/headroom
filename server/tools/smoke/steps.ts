/** The caller owns the populated seed, production server, browser and restart. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import type { public_event } from '../../conversation/http/record.ts';

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
  model: string;
}
const execute = promisify(execFile);
const attachment = 'Private production smoke attachment.\n';
const title = 'Production smoke Ticket';
const log: Step = (name, evidence) => console.info(name, JSON.stringify(evidence ?? {}));
function client(base: string) {
  const url = new URL(base);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, '127.0.0.1');
  assert.ok(url.port && url.port !== '0');
  assert.equal(url.username + url.password + url.search + url.hash, '');
  assert.equal(url.pathname, '/');
  return async <T = Json>(path: string, body?: unknown, headers = {}): Promise<T> => {
    const response = await fetch(url.origin + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: 'error',
      signal: AbortSignal.timeout(15_000),
    });
    const output = await response.text();
    assert.ok(response.ok, `${path}: ${response.status}: ${output}`);
    return JSON.parse(output) as T;
  };
}
async function cli_ticket(base: string, id: string, environment: NodeJS.ProcessEnv): Promise<Json> {
  assert.ok(environment.PLAN_APP_ROOT);
  const { stdout } = await execute(
    process.execPath,
    [join(environment.PLAN_APP_ROOT, 'server/cli/main.ts'), 'ticket', 'show', id, '--json'],
    {
      env: {
        ...environment,
        PLAN_SERVER_URL: new URL(base).origin,
        PLAN_ACTOR: '',
        PLAN_TICKET_ID: '',
        PLAN_MANAGER_ID: '',
        PLAN_CONVERSATION_ID: '',
      },
      timeout: 15_000,
      killSignal: 'SIGKILL',
      maxBuffer: 1024 * 1024,
    },
  );
  const ticket = (JSON.parse(stdout) as { header: Json }).header;
  assert.equal(ticket.id, id);
  assert.equal(ticket.title, title);
  return ticket;
}
async function poll<T>(read: () => Promise<T>, accepted: (value: T) => boolean): Promise<T> {
  const deadline = performance.now() + 15_000;
  while (true) {
    const value = await read();
    if (accepted(value)) return value;
    assert.ok(performance.now() < deadline, 'Production smoke observation timed out');
    await delay(100);
  }
}
async function review_gate(base: string, ticket_id: string): Promise<void> {
  const api = client(base);
  const review = await api<{ items: Json[] }>('/api/review');
  assert.ok(
    review.items.some(
      (item) => item.ticket_id === ticket_id && item.review_item_type === 'ticket' && item.proposal,
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
async function download(base: string, conversation: string, file: string): Promise<void> {
  client(base);
  const response = await fetch(
    `${new URL(base).origin}/api/conversation/conversations/${conversation}/files/${file}`,
    { redirect: 'error', signal: AbortSignal.timeout(15_000) },
  );
  assert.equal(response.status, 200);
  assert.ok(response.headers.get('content-type')?.startsWith('text/plain'));
  assert.equal(await response.text(), attachment);
}
export async function smoke_before_restart(
  base: string,
  environment: NodeJS.ProcessEnv,
  options: SmokeOptions,
): Promise<SmokeState> {
  const api = client(base),
    step = options.step ?? log;
  const populated = await api<{ tickets: Json[] }>('/api/tickets?detail=summary&limit=5');
  assert.ok(populated.tickets.length > 0, 'Expected the populated synthetic seed');
  step('01 populated production reads');
  const ticket = await api('/api/tickets', { title, worker_type: 'coding', locale: 'vps' });
  assert.ok(typeof ticket.id === 'string');
  const ticket_id = ticket.id;
  await cli_ticket(base, ticket_id, environment);
  step('02 HTTP create and executed TypeScript CLI read Ticket', { ticket_id });
  const proposed = await api(
    `/api/tickets/${ticket_id}/propose`,
    {
      body: 'Production smoke proposal ready for owner review.',
    },
    { 'x-plan-actor': 'worker', 'x-plan-ticket-id': ticket_id },
  );
  assert.equal(proposed.ticket_status, 'awaiting_approval');
  await review_gate(base, ticket_id);
  step('03 proposal opens owner review notification and attention');

  const conversation_id = 'smoke-' + randomUUID();
  const conversation = `/api/conversation/conversations/${conversation_id}`;
  const started = await api('/api/conversation/conversations', {
    conversation_id,
    backend_key: 'codex',
    model: options.model,
    workspace_folder: options.workspace,
  });
  assert.equal(started.model, options.model);
  step('04 manual conversation through production adapter', { conversation_id });
  const events = await (async () => {
    const lifetime = new AbortController();
    const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(15_000)]);
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let live_reply: Promise<void> | undefined;
    try {
      const tail = await fetch(new URL(base).origin + conversation + '/tail', {
        redirect: 'error',
        signal,
      });
      assert.equal(tail.status, 200);
      assert.ok(tail.headers.get('content-type')?.startsWith('text/event-stream'));
      assert.ok(tail.body);
      reader = tail.body.getReader();
      const owned_reader = reader;
      live_reply = (async () => {
        const decoder = new TextDecoder();
        let pending = '',
          reply = '';
        while (true) {
          const chunk = await owned_reader.read();
          assert.ok(!chunk.done, 'Live tail ended before the reply');
          pending += decoder.decode(chunk.value, { stream: true });
          let boundary: number;
          while ((boundary = pending.indexOf('\n\n')) >= 0) {
            const frame = pending.slice(0, boundary);
            pending = pending.slice(boundary + 2);
            if (!frame.startsWith('event: conversation-frame\n')) continue;
            const data = frame.split('\n').find((line) => line.startsWith('data: '));
            assert.ok(data);
            const value = JSON.parse(data.slice(6)) as Json;
            if (value.frame !== 'agent_message_delta') continue;
            assert.ok(typeof value.text_delta === 'string');
            reply += value.text_delta;
            if (reply === options.reply) return;
          }
        }
      })();
      void live_reply.catch(() => {});
      const sent = await api(conversation + '/send', {
        content: [
          { piece: 'text', text: 'Production smoke prompt.' },
          {
            piece: 'file',
            file_name: 'smoke.txt',
            media_type: 'text/plain',
            data: Buffer.from(attachment).toString('base64'),
          },
        ],
        sender_label: 'Owner',
        sender_message_id: 'smoke-send',
      });
      assert.equal(sent.fate, 'started');
      await live_reply;
      const completed = await poll(
        async () => {
          const events = await api<{ events: Event[] }>(conversation + '/events');
          const state = await api(conversation);
          return { events: events.events, running: state.is_running };
        },
        (value) =>
          value.running === false &&
          value.events.some(
            (event) =>
              event.kind === 'turn_ended' &&
              event.payload.ending === 'completed' &&
              event.payload.error_summary === null,
          ),
      );
      return completed.events;
    } finally {
      lifetime.abort();
      await Promise.allSettled([reader?.cancel(), live_reply]);
      reader?.releaseLock();
    }
  })();
  step('05 combined text/file send has live reply and completed recorded turn');
  const uploaded = events.find(
    (event) => event.kind === 'prompt' && event.payload.sender_message_id === 'smoke-send',
  );
  assert.ok(uploaded);
  const pieces = uploaded.payload.content as Json[];
  assert.ok(
    pieces.some((piece) => piece.piece === 'text' && piece.text === 'Production smoke prompt.'),
  );
  const file = pieces.find((piece) => piece.piece === 'file' && piece.file_name === 'smoke.txt');
  assert.ok(file && typeof file.stored_file_id === 'string');
  const stored_file_id = file.stored_file_id;
  await download(base, conversation_id, stored_file_id);
  step('06 uploaded file bytes round trip', { stored_file_id });
  const goal = await api('/api/goals', {
    title: 'Production smoke goal',
    project_id: 'project_other',
    kind: 'binary',
    primary_checklist_block_id: null,
  });
  assert.ok(typeof goal.id === 'string');
  const goal_id = goal.id;
  step('07 create goal', { goal_id });
  return { ticket_id, conversation_id, goal_id, stored_file_id, events, model: options.model };
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
  const replay = await api<{ events: Event[] }>(conversation + '/events');
  assert.deepEqual(replay.events.slice(0, state.events.length), state.events);
  step('08 restart preserves conversation, prompt and completed turn');
  await download(base, state.conversation_id, state.stored_file_id);
  const goal = await api(`/api/goals/${state.goal_id}`);
  assert.equal(goal.id, state.goal_id);
  assert.equal(goal.title, 'Production smoke goal');
  step('09 restart preserves uploaded bytes and goal');
  assert.equal(
    (await cli_ticket(base, state.ticket_id, environment)).ticket_status,
    'awaiting_approval',
  );
  await review_gate(base, state.ticket_id);
  step('10 CLI and owner review notification/attention survive restart');
}
