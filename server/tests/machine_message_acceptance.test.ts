/** Risk: a message waits for its recipient's native work before home accepts or shows it.
 * Incidents: a Chief correction waited ~4 minutes behind a Worker launch, and a Worker message
 * waited ~2 hours behind an owner PNG download. recipient_delivery.test.ts proves admission
 * order in the scripted core only; no test sends through the real CLI, machine outbox and
 * home routes while a machine-side launch or download is held. One private home and one real
 * machine invocation (the machine_restart fixture, socket transport) serve both cases.
 */
import { afterAll, beforeAll, expect, test, vi } from 'vitest';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { setTimeout as pause } from 'node:timers/promises';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { start_native_server, NATIVE_CODEX_MODEL } from '../executor/mac/testing/native_server.ts';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { credential_path, write_credential } from '../executor/credential_file.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { OWNER } from '../core/contracts.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  PromptDeliveryStarted,
  WorkspacePathPolicy,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { create_ticket, write_ticket_conversation_start } from '../tickets/data/index.ts';
import { start_ready_worker_step } from '../runtime/worker_step_readiness_loop.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import type { JsonObject } from '../executor/wire.generated.ts';

const CLI = fileURLToPath(new URL('../cli/main.ts', import.meta.url));
const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

async function until<T>(read: () => T | Promise<T>, accepts: (value: T) => boolean): Promise<T> {
  const signal = AbortSignal.timeout(20_000);
  for (;;) {
    signal.throwIfAborted();
    const value = await read();
    if (accepts(value)) return value;
    await pause(50, undefined, { signal });
  }
}

let root = '';
const owner = new ChildProcesses();
let server: Awaited<ReturnType<typeof start_native_server>> | undefined;
let peer: ReturnType<ChildProcesses['spawn']> | undefined;
let machine_root = '';
let agent_environment: NodeJS.ProcessEnv = {};
let peer_error = '';
let serial = 0;
const pending = new Map<number, { resolve(value: JsonObject): void; reject(error: Error): void }>();
let chief_conversation = '';
let worker = { ticket_id: '', conversation_id: '' };

function command(operation: string, values: JsonObject = {}): Promise<JsonObject> {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    peer!.stdin!.write(JSON.stringify({ id, operation, ...values }) + '\n');
  });
}
const calls = async () => (await command('state')).calls as Record<string, any>;

/** The real CLI process, as an agent under machine code runs it. */
async function panels(conversation_id: string, args: string[]): Promise<string> {
  const state = await command('state');
  const child = (state.children as JsonObject[]).find(
    (item) => (item.reference as JsonObject).conversation_id === conversation_id,
  );
  assert.ok(child, `no machine child for ${conversation_id}`);
  const process_ = owner.spawn(process.execPath, [CLI, ...args], {
    cwd: root,
    env: {
      ...agent_environment,
      PLAN_EXECUTOR_ROOT: machine_root,
      PLAN_EXECUTOR_CHILD_ID: String((child.reference as JsonObject).child_id),
      PLAN_EXECUTOR_CAPABILITY: String(child.capability),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '',
    stderr = '';
  process_.stdout!.on('data', (chunk) => (stdout += String(chunk)));
  process_.stderr!.on('data', (chunk) => (stderr += String(chunk)));
  const exit = new Promise<number | null>((resolve) => process_.once('exit', resolve));
  // A send that waits for its recipient's native work would block far longer than this.
  const code = await Promise.race([exit, pause(15_000).then(() => 'timeout' as const)]);
  if (code === 'timeout') {
    process_.kill('SIGKILL');
    await exit;
    throw new Error(`panels ${args.join(' ')} did not answer in 15 s: ${stderr}`);
  }
  expect({ code, stderr }).toEqual({ code: 0, stderr: '' });
  return stdout.trim();
}

async function http(path: string, init?: RequestInit): Promise<any> {
  const response = await fetch(server!.address + path, {
    ...init,
    headers: { 'content-type': 'application/json' },
    signal: AbortSignal.timeout(10_000),
  });
  const body = await response.json();
  if (response.status !== 200)
    throw new Error(`${path} ${response.status}: ${JSON.stringify(body)}`);
  return body;
}
/** Pending rows the conversation read shows: the queue, and accepted messages not yet placed. */
async function pending_rows(
  conversation_id: string,
): Promise<{ sender_message_id: string; requested_mode: string }[]> {
  const read = await http(`/api/conversation/conversations/${conversation_id}`);
  return [...read.held_prompts, ...read.pending_messages].map(
    ({ sender_message_id, requested_mode }) => ({ sender_message_id, requested_mode }),
  );
}
const pending_ids = async (conversation_id: string) =>
  (await pending_rows(conversation_id)).map((row) => row.sender_message_id);
/** An open pane: the view stream's waiting-messages revision on every frame. */
function open_pane(conversation_id: string) {
  const controller = new AbortController();
  const revisions: number[] = [];
  const reading = (async () => {
    const response = await fetch(
      `${server!.address}/api/conversation/conversations/${conversation_id}/view/tail?lens=focus`,
      { signal: controller.signal },
    );
    expect(response.status).toBe(200);
    const decoder = new TextDecoder();
    let buffered = '';
    const reader = response.body!.getReader();
    for (let read = await reader.read(); !read.done; read = await reader.read()) {
      buffered += decoder.decode(read.value, { stream: true });
      for (let end = buffered.indexOf('\n\n'); end >= 0; end = buffered.indexOf('\n\n')) {
        const data = buffered
          .slice(0, end)
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5))
          .join('\n');
        buffered = buffered.slice(end + 2);
        if (data) revisions.push(JSON.parse(data).status.waiting_messages_revision);
      }
    }
  })().catch((error) => {
    if (!controller.signal.aborted) throw error;
  });
  return {
    revisions,
    opened: () =>
      until(
        () => revisions,
        (seen) => seen.length > 0,
      ).then((seen) => seen[0]),
    changed: (from: number) =>
      until(
        () => revisions,
        (seen) => seen.some((value) => value > from),
      ),
    async close() {
      controller.abort();
      await reading;
    },
  };
}
const view_text = async (conversation_id: string): Promise<string> =>
  JSON.stringify(
    (await http(`/api/conversation/conversations/${conversation_id}/view?lens=focus`)).page.items,
  );
const events_for = (conversation_id: string, sender_message_id: string) =>
  server!.context.record.store
    .read_events_after(conversation_id, 0)
    .filter(
      (event) =>
        (event.payload as { sender_message_id?: string }).sender_message_id === sender_message_id,
    )
    .map((event) => ({ kind: event.kind, mode: (event.payload as { mode?: string }).mode }));

async function start_agent_conversation(
  conversation_id: string,
  identity: [string, string][],
): Promise<void> {
  await server!.core.start_conversation(
    new ConversationStartRequest({
      conversation_id,
      backend_key: parse_backend_instance_id('codex'),
      model: NATIVE_CODEX_MODEL,
      reasoning_effort: null,
      role_materials: new ConversationRoleMaterials({
        role_text: 'Run only the scripted acceptance proof.',
        identity_environment_variables: identity,
      }),
      workspace_folder: '/native-rehearsal/' + conversation_id,
      workspace_path_policy: WorkspacePathPolicy.executor_default,
      access: 'full',
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    }),
  );
}

beforeAll(async () => {
  root = await mkdtemp(join(tmpdir(), 'accept-'));
  const database = join(root, 'home.db');
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: database,
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
  }))
    vi.stubEnv(key, value);
  server = await start_native_server();
  machine_root = join(root, 'machine');
  await write_credential(credential_path(machine_root), server.credential);
  const home = join(root, 'provider-home');
  await mkdir(join(home, 'skills'), { recursive: true });
  // No inherited PLAN_ value may reach the machine or the CLI: only this private home.
  agent_environment = { ...process.env, HOME: home, CODEX_HOME: home, CLAUDE_CONFIG_DIR: home };
  for (const name of Object.keys(agent_environment))
    if (name.startsWith('PLAN_')) delete agent_environment[name];
  const input = join(root, 'machine.json');
  await writeFile(
    input,
    JSON.stringify({
      origin: executor_link_socket_path(database),
      fixture_root: root,
      fixture_database_path: database,
      root: machine_root,
      revision: 'acceptance-proof',
      catalog_model: NATIVE_CODEX_MODEL,
      environment: agent_environment,
    }),
  );
  peer = owner.spawn(
    process.execPath,
    [fileURLToPath(new URL('../executor/mac/testing/end_to_end.ts', import.meta.url)), input],
    { env: agent_environment, stdio: ['pipe', 'pipe', 'pipe'] },
  );
  peer.stderr!.on('data', (chunk) => (peer_error += String(chunk)));
  peer.once('exit', (code) => {
    for (const waiter of pending.values())
      waiter.reject(new Error(`Machine exited ${code}: ${peer_error}`));
    pending.clear();
  });
  let ready!: () => void;
  const readiness = new Promise<void>((resolve) => (ready = resolve));
  createInterface({ input: peer.stdout! }).on('line', (line) => {
    const response = JSON.parse(line) as {
      ready?: boolean;
      id: number;
      result: JsonObject;
      error?: string;
    };
    if (response.ready) return ready();
    const waiter = pending.get(response.id);
    pending.delete(response.id);
    if (response.error) waiter?.reject(new Error(response.error));
    else waiter?.resolve(response.result);
  });
  await Promise.race([
    readiness,
    pause(35_000).then(() => {
      throw new Error(`Machine startup timeout: ${peer_error}`);
    }),
  ]);
  await server.wait_for_presence();

  // The Chief runs under machine code with a running first turn.
  chief_conversation = 'chief_' + randomUUID().replaceAll('-', '');
  await start_agent_conversation(chief_conversation, [['PLAN_ACTOR', 'chief']]);
  server.context.database.write((tx) =>
    tx.run(
      'INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id',
      ['chief_of_staff', chief_conversation],
    ),
  );
  expect(
    await server.core.send(chief_conversation, text_message_content('[CASE:plain]'), {
      sender_label: 'owner',
    }),
  ).toBeInstanceOf(PromptDeliveryStarted);
  await until(calls, (value) => value.prompts === 1);

  // The Worker's conversation is linked. Its launch starts with the Worker step below.
  const ticket = server.context.database.write((tx) =>
    create_ticket(tx, {
      title: 'Wren launch',
      principal: OWNER,
      now: server!.context.clock.nowUnix(),
      title_max_chars: 200,
      worker_type: 'coding',
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      employee_backend: 'codex',
      employee_launch_model: NATIVE_CODEX_MODEL,
      employee_launch_reasoning_effort: null,
      reasoning_effort_is_set: true,
      model_source_is_historical: true,
    }),
  );
  worker = { ticket_id: ticket.id, conversation_id: 'worker_' + randomUUID().replaceAll('-', '') };
  await start_agent_conversation(worker.conversation_id, [
    ['PLAN_ACTOR', 'worker'],
    ['PLAN_TICKET_ID', ticket.id],
  ]);
  server.context.database.write((tx) =>
    write_ticket_conversation_start(tx, ticket.id, {
      conversation_id: worker.conversation_id,
      backend: 'codex',
      model: NATIVE_CODEX_MODEL,
      reasoning_effort: null,
      now: server!.context.clock.nowUnix(),
    }),
  );
}, 90_000);

afterAll(async () => {
  try {
    if (peer && peer.exitCode === null) {
      const closed = new Promise((resolve) => peer!.once('close', resolve));
      await command('stop').catch(() => {});
      // A group whose leader is exiting answers SIGKILL with EPERM on Darwin.
      await Promise.race([closed, pause(20_000)]);
    }
  } finally {
    try {
      await server?.stop();
      owner.force_kill(performance.now() / 1000 + 20);
      await owner.settled();
    } finally {
      if (root) await rm(root, { recursive: true, force: true });
      vi.unstubAllEnvs();
    }
  }
}, 60_000);

test('a Chief correction is accepted, shown and answered around a held Worker launch', async () => {
  const started = performance.now();
  await command('configure', { hold_start: true });
  // The Worker step takes the Ticket link lock across its first prompt, as the incident did.
  let kickoff_settled = false;
  const kickoff = start_ready_worker_step(worker.ticket_id, {
    connect_database: () => server!.context.database,
    conversation_system: server!.core,
    worker_type_registry: configured_worker_type_registry(),
    now: () => server!.context.clock.nowUnix(),
  }).finally(() => (kickoff_settled = true));
  // A failed assertion leaves the launch held; shutdown then aborts it.
  kickoff.catch(() => {});
  await until(calls, (value) => value.held_starts.includes(worker.conversation_id));
  expect(kickoff_settled).toBe(false);

  // Default mode is steer, the Chief's ordinary correction.
  const worker_pane = open_pane(worker.conversation_id);
  const worker_revision = await worker_pane.opened();
  const correction = 'Correction: use the second checkout.';
  const answer = await panels(chief_conversation, [
    'send-message',
    '--ticket',
    worker.ticket_id,
    '--message',
    correction,
  ]);
  const correction_id = /^accepted for delivery · (\S+)$/.exec(answer)?.[1];
  expect(answer).toMatch(/^accepted for delivery · \S+$/);
  await vi.waitFor(
    async () =>
      expect(await pending_rows(worker.conversation_id)).toContainEqual({
        sender_message_id: correction_id,
        requested_mode: 'steer',
      }),
    { timeout: 5_000 },
  );
  await worker_pane.changed(worker_revision);
  await worker_pane.close();

  const reply = 'Sent the Worker a correction.';
  const recorded = await panels(chief_conversation, [
    'send-message',
    '--owner',
    '--message',
    reply,
  ]);
  expect(recorded).toMatch(/^recorded · \S+$/);
  expect(await view_text(chief_conversation)).toContain(reply);

  // Everything above happened while the launch was still held.
  const held = await calls();
  expect(held.held_starts).toContain(worker.conversation_id);
  expect(held.starts).toBe(1);
  expect(kickoff_settled).toBe(false);
  const held_seconds = (performance.now() - started) / 1000;

  await command('configure', { hold_start: false });
  expect((await kickoff).started).toBe(true);
  await until(calls, (value) =>
    value.deliveries.some((item: any) => item.conversation_id === worker.conversation_id),
  );
  // A steer lands in the running first turn. Ending that turn would show a drain duplicate.
  await until(
    () => events_for(worker.conversation_id, correction_id!),
    (events) => events.length > 0,
  );
  await command('emit', { kind: 'end', conversation_id: worker.conversation_id });
  await until(
    () => server!.core.is_running(worker.conversation_id),
    (running) => !running,
  );
  await pause(500);
  // The first turn was running, so the default steer landed in it.
  expect(events_for(worker.conversation_id, correction_id!)).toEqual([
    { kind: 'prompt', mode: 'steer' },
  ]);
  expect(await pending_ids(worker.conversation_id)).not.toContain(correction_id);
  const delivered = await calls();
  const carried = [
    ...delivered.steer_texts.filter((item: any) => item.conversation_id === worker.conversation_id),
    ...delivered.deliveries.filter((item: any) => item.conversation_id === worker.conversation_id),
  ].filter((item: any) => item.text.includes(correction));
  expect(carried).toHaveLength(1);
  expect(delivered.steer_texts).toContainEqual({
    conversation_id: worker.conversation_id,
    text: expect.stringContaining(correction),
  });
  console.log(
    JSON.stringify({
      case: 'held launch',
      held_seconds,
      seconds: (performance.now() - started) / 1000,
    }),
  );
}, 60_000);

test('a Worker message is accepted, shown and answered around a stalled owner PNG download', async () => {
  const started = performance.now();
  // The Chief is idle, so the owner's prompt starts a native turn that downloads its picture.
  await command('emit', { kind: 'end', conversation_id: chief_conversation });
  await until(
    () => server!.core.is_running(chief_conversation),
    (running) => !running,
  );
  await command('configure', { hold_download: true, fail_download: true });
  const owner_id = randomUUID();
  const owner_answer = await http('/api/chief/conversation/send', {
    method: 'POST',
    body: JSON.stringify({
      content: [
        { piece: 'text', text: 'What does this screenshot show?' },
        { piece: 'image', data: PNG, media_type: 'image/png', file_name: 'shot.png' },
      ],
      sender_label: 'owner',
      sender_message_id: owner_id,
    }),
  });
  expect(owner_answer).toMatchObject({ fate: 'accepted', message_id: owner_id });
  await until(calls, (value) => value.downloads.length === 1);
  const outcome = async () =>
    (await http(`/api/conversation/owner-sends/${owner_id}?owner_kind=chief&owner_id=chief`))
      .outcome;
  expect(await outcome()).toMatchObject({ outcome: 'pending' });

  const chief_pane = open_pane(chief_conversation);
  const chief_revision = await chief_pane.opened();
  const note = 'Worker note: the second checkout is ready.';
  const answer = await panels(worker.conversation_id, [
    'send-message',
    '--chief',
    '--message',
    note,
  ]);
  const note_id = /^accepted for delivery · (\S+)$/.exec(answer)?.[1];
  expect(answer).toMatch(/^accepted for delivery · \S+$/);
  await vi.waitFor(
    async () =>
      expect(await pending_rows(chief_conversation)).toContainEqual({
        sender_message_id: note_id,
        requested_mode: 'steer',
      }),
    { timeout: 5_000 },
  );
  await chief_pane.changed(chief_revision);
  await chief_pane.close();

  const reply = 'Told the Chief the checkout is ready.';
  const recorded = await panels(worker.conversation_id, [
    'send-message',
    '--owner',
    '--message',
    reply,
  ]);
  expect(recorded).toMatch(/^recorded · \S+$/);
  expect(await view_text(worker.conversation_id)).toContain(reply);

  // Everything above happened while the download was still stalled.
  expect((await calls()).downloads).toHaveLength(1);
  expect(await outcome()).toMatchObject({ outcome: 'pending' });
  const held_seconds = (performance.now() - started) / 1000;

  // Release into the incident's failure. The bounded retry gives up after three attempts.
  await command('configure', { hold_download: false });
  const settled = await until(outcome, (value) => value.outcome !== 'pending');
  expect((await calls()).downloads).toHaveLength(3);
  expect(settled).toMatchObject({ outcome: 'refused' });
  await until(calls, (value) =>
    value.deliveries.some(
      (item: any) => item.conversation_id === chief_conversation && item.text.includes(note),
    ),
  );
  await command('emit', { kind: 'end', conversation_id: chief_conversation });
  await until(
    () => server!.core.is_running(chief_conversation),
    (running) => !running,
  );
  await pause(500);
  // The refused head prompt left no turn to steer into, so the steer started a new turn.
  expect(events_for(chief_conversation, note_id!)).toEqual([{ kind: 'prompt', mode: 'queue' }]);
  expect(await pending_ids(chief_conversation)).not.toContain(note_id);
  const delivered = await calls();
  const carried = (list: any[]) =>
    list.filter((item) => item.conversation_id === chief_conversation && item.text.includes(note));
  expect(carried(delivered.deliveries)).toHaveLength(1);
  expect(carried(delivered.steer_texts)).toHaveLength(0);
  console.log(
    JSON.stringify({
      case: 'stalled download',
      held_seconds,
      outcome: settled,
      seconds: (performance.now() - started) / 1000,
    }),
  );
}, 60_000);
