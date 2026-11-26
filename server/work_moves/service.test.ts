/** Gap: work relocation spans cleanup proof, identity, workflow gates and the actual prompt.
 * The existing SQLite fixture and in-memory conversation host prove this without a browser.
 */
import { dirname } from 'node:path';
import { expect, test, vi } from 'vitest';
import Fastify from 'fastify';
import {
  fixture,
  fixture_path,
  create,
  read,
  propose,
  manager,
  now,
} from '../tests/ticket_fixture.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  configure_conversation_start,
  worker_resolve,
  start_ticket_conversation,
  read_agent_conversation,
  reset_agent_conversation,
  agent_resolve,
  manager_resolve,
} from '../runtime/conversation_start.ts';
import { seed_machine_lease } from '../machines/testing/lease.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { read_manager } from '../managers/data.ts';
import {
  issue_launch_token,
  authenticate_launch_token,
  canonical_principal,
} from '../agent_identity/data.ts';
import { move_work, move_everything } from './service.ts';
import { agent_machine_id } from '../machines/conversation_machine.ts';
import { work_move_status, unfinished_work } from './data.ts';
import {
  recipient_lifecycle,
  require_recipient_available,
} from '../message_delivery/data/lifecycles.ts';
import { routes } from './routes.ts';
import type { WorkMoveProof } from './contracts.ts';
import { OWNER, CHIEF, PlannerError } from '../core/contracts.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import type { VpsExecutorRegistry } from '../executor/registry.ts';

function setup() {
  const database = fixture();
  configure_conversation_start(database, {
    db_path: fixture_path(database),
    workspace_root: dirname(fixture_path(database)),
  });
  database.write((tx) => seed_machine_lease(tx, ORIGINAL_MAC_MACHINE_ID));
  const system = new InMemoryConversationSystem();
  const registry = {
    work_move_source: vi.fn(async (): Promise<WorkMoveProof> => ({
      published_branch: 'ticket/published-work',
      published_revision: 'a'.repeat(40),
    })),
  };
  return { database, system, registry, now: () => now };
}

test('Ticket move preserves its parked proposal and pause, revokes old authority, and sends the record to a fresh destination conversation', async () => {
  const services = setup();
  const { database, system, registry } = services;
  let ticket = create(database);
  ticket = propose(database, ticket, 'Proposal that must remain parked');
  database.write((tx) => tx.run('UPDATE tickets SET is_backlogged=1 WHERE id=?', [ticket.id]));
  ticket = read(database, ticket.id);
  const old = await start_ticket_conversation(
    system,
    database,
    ticket,
    worker_resolve(database, ticket),
    { conversation_id: 'conv_source', now },
  );
  const token = issue_launch_token(database, old.conversation_id);
  const kill = vi.spyOn(system, 'kill');
  registry.work_move_source.mockImplementation(async () => {
    expect(kill).toHaveBeenCalledWith(old.conversation_id);
    expect(read(database, ticket.id).conversation_id).toBe(old.conversation_id);
    return { published_branch: 'ticket/published-work', published_revision: 'a'.repeat(40) };
  });
  const before = read(database, ticket.id);
  const result = await move_work(services, {
    owner: { kind: 'ticket', id: ticket.id },
    machine_id: ORIGINAL_MAC_MACHINE_ID,
  });
  expect(result).toMatchObject({ moved: true, error: null });
  const after = read(database, ticket.id);
  expect(after).toMatchObject({
    stage: before.stage,
    ceiling: before.ceiling,
    ceiling_holder: before.ceiling_holder,
    manager_id: before.manager_id,
    is_backlogged: true,
    pending_proposal: before.pending_proposal,
    field_values: before.field_values,
    machine_id: ORIGINAL_MAC_MACHINE_ID,
  });
  expect(after.conversation_id).not.toBe(old.conversation_id);
  expect(database.read((sql) => authenticate_launch_token(sql, token))).toBeNull();
  expect(database.read((sql) => canonical_principal(sql, old.conversation_id))).toBeNull();
  const prompts = system
    .observations(after.conversation_id!)
    .map((observation) => observation.text)
    .filter(Boolean)
    .join('\n');
  expect(prompts).toContain('Proposal that must remain parked');
  expect(prompts).toContain('Wait for the next authorized Worker step');
  expect(
    database.read((sql) =>
      sql.get('SELECT published_branch,published_revision FROM tickets WHERE id=?', [ticket.id]),
    ),
  ).toEqual({ published_branch: 'ticket/published-work', published_revision: 'a'.repeat(40) });
});

test('source unavailability, unproven cleanup and unpublished work never change the destination or unlink the source', async () => {
  const services = setup();
  const { database, system, registry } = services;
  const ticket = create(database);
  await start_ticket_conversation(system, database, ticket, worker_resolve(database, ticket), {
    conversation_id: 'conv_source',
    now,
  });
  const kill = vi.spyOn(system, 'kill');
  database.write((tx) =>
    tx.run(
      "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.supports_work_moves',json('false')) WHERE machine_id=?",
      [ORIGINAL_MAC_MACHINE_ID],
    ),
  );
  expect(
    await move_work(services, {
      owner: { kind: 'ticket', id: ticket.id },
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    }),
  ).toMatchObject({
    moved: false,
    error: 'Update source and destination machine code before a move.',
  });
  expect(kill).not.toHaveBeenCalled();
  expect(registry.work_move_source).not.toHaveBeenCalled();
  database.write((tx) =>
    tx.run(
      "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.supports_work_moves',json('true')) WHERE machine_id=?",
      [ORIGINAL_MAC_MACHINE_ID],
    ),
  );
  database.write((tx) =>
    tx.run('UPDATE mac_executor_presence SET last_lease_at_ms=0 WHERE machine_id=?', [
      HOME_MACHINE_ID,
    ]),
  );
  expect(
    await move_work(services, {
      owner: { kind: 'ticket', id: ticket.id },
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    }),
  ).toMatchObject({ moved: false, error: expect.any(String) });
  expect(registry.work_move_source).not.toHaveBeenCalled();
  database.write((tx) =>
    tx.run('UPDATE mac_executor_presence SET last_lease_at_ms=? WHERE machine_id=?', [
      Number.MAX_SAFE_INTEGER,
      HOME_MACHINE_ID,
    ]),
  );
  kill.mockRejectedValueOnce(new Error('cleanup remains unknown'));
  expect(
    await move_work(services, {
      owner: { kind: 'ticket', id: ticket.id },
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    }),
  ).toMatchObject({ moved: false, error: 'cleanup remains unknown' });
  expect(registry.work_move_source).not.toHaveBeenCalled();
  kill.mockRestore();
  registry.work_move_source.mockRejectedValueOnce(new Error('unpushed work'));
  expect(
    await move_work(services, {
      owner: { kind: 'ticket', id: ticket.id },
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    }),
  ).toMatchObject({ moved: false, error: 'unpushed work' });
  expect(read(database, ticket.id)).toMatchObject({
    machine_id: HOME_MACHINE_ID,
    conversation_id: 'conv_source',
  });
  const target = { kind: 'ticket' as const, id: ticket.id };
  expect(() => database.read((sql) => require_recipient_available(sql, target))).toThrow(
    expect.objectContaining({ code: 'validation' }),
  );
  // Failed cleanup remains fenced, but the same explicit move can prove cleanup and recover.
  expect(
    await move_work(services, { owner: target, machine_id: ORIGINAL_MAC_MACHINE_ID }),
  ).toMatchObject({ moved: true, error: null });
  expect(database.read((sql) => recipient_lifecycle(sql, target))).toMatchObject({ retiring: 0 });
  expect(() => database.read((sql) => require_recipient_available(sql, target))).not.toThrow();
});

test('Chief and Manager moves retain their own destination after reset and receive their own record', async () => {
  const services = setup();
  const { database, system, registry } = services;
  registry.work_move_source.mockResolvedValue({ published_branch: null, published_revision: null });
  const id = manager(database, 'Move this Manager');
  database.write((tx) => {
    tx.run('INSERT OR IGNORE INTO agents(agent_key,conversation_id) VALUES (?,NULL)', [
      `manager_${id}`,
    ]);
    tx.run("UPDATE managers SET goal='Keep this goal' WHERE id=?", [id]);
  });
  for (const owner of [{ kind: 'manager' as const, id }, { kind: 'chief' as const }]) {
    const result = await move_work(services, { owner, machine_id: ORIGINAL_MAC_MACHINE_ID });
    expect(result).toMatchObject({ moved: true, error: null });
    const key = owner.kind === 'chief' ? 'chief_of_staff' : `manager_${id}`;
    const conversation = read_agent_conversation(database, key)!;
    const prompt = system
      .observations(conversation)
      .map((observation) => observation.text)
      .join('\n');
    expect(prompt).toContain(owner.kind === 'manager' ? 'Keep this goal' : 'current Chief page');
    await reset_agent_conversation(system, database, key);
    expect(database.read((sql) => agent_machine_id(sql, owner))).toBe(ORIGINAL_MAC_MACHINE_ID);
    expect(
      owner.kind === 'chief'
        ? agent_resolve(database).machine_id
        : manager_resolve(
            database,
            database.read((sql) => read_manager(sql, id).item),
          ).machine_id,
    ).toBe(ORIGINAL_MAC_MACHINE_ID);
  }
});

test('bulk moves require a completed server move and preserve individual refusals without moving historical work', async () => {
  const services = setup();
  const { database, registry } = services;
  const ticket = create(database);
  const done = create(database);
  database.write((tx) => tx.run("UPDATE tickets SET stage='done' WHERE id=?", [done.id]));
  const settled = manager(database, 'Settled Manager');
  database.write((tx) => tx.run('UPDATE managers SET settled=1 WHERE id=?', [settled]));
  expect(database.read(work_move_status)).toEqual({ move_everything_machine_id: null });
  await expect(move_everything(services, ORIGINAL_MAC_MACHINE_ID)).rejects.toThrow(
    'after a successful server move',
  );
  database.write((tx) => {
    tx.run(
      "INSERT INTO server_link(singleton,server_id,move_number,server_url,state) VALUES(1,'test-server',1,'https://new.example','active')",
    );
    tx.run('UPDATE machines SET is_home=CASE WHEN machine_id=? THEN 1 ELSE 0 END', [
      ORIGINAL_MAC_MACHINE_ID,
    ]);
  });
  expect(database.read(unfinished_work)).toContainEqual({ kind: 'ticket', id: ticket.id });
  expect(database.read(unfinished_work)).not.toContainEqual({ kind: 'ticket', id: done.id });
  expect(database.read(unfinished_work)).not.toContainEqual({ kind: 'manager', id: settled });
  registry.work_move_source.mockRejectedValue(new Error('Push source work first'));
  const result = await move_everything(services, ORIGINAL_MAC_MACHINE_ID);
  expect(
    result.results.every((entry) => !entry.moved && entry.error === 'Push source work first'),
  ).toBe(true);
  expect(read(database, ticket.id).machine_id).toBe(HOME_MACHINE_ID);
  // Reuse this bulk fixture to prove partial background results persist independently.
  const { create_operation, read_operations, run_operation } = await import('./operations.ts');
  registry.work_move_source.mockReset();
  registry.work_move_source.mockRejectedValueOnce(new Error('Push source work first'));
  registry.work_move_source.mockResolvedValue({ published_branch: null, published_revision: null });
  const operation = create_operation(services, ORIGINAL_MAC_MACHINE_ID);
  await run_operation(services, operation);
  const persisted = database.read(read_operations)[0]!;
  expect(persisted.finished_at_ms).not.toBeNull();
  expect(persisted.items[0]!.result).toMatchObject({
    moved: false,
    error: 'Push source work first',
  });
  expect(
    persisted.items.slice(1).every((item) => item.result?.error === null && item.result?.moved),
  ).toBe(true);
});

test('work move routes refuse an agent before any machine request', async () => {
  const services = setup();
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(services.database),
  });
  const app = Fastify();
  app.decorate('executor_registry', services.registry as unknown as VpsExecutorRegistry);
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  let principal = CHIEF;
  await app.register(routes, {
    database: services.database,
    config,
    clock: buildClock(config),
    conversationSystem: services.system,
    identity: () => ({ principal, conversationId: null }),
  });
  try {
    const payload = { owner: { kind: 'chief' }, machine_id: ORIGINAL_MAC_MACHINE_ID };
    expect((await app.inject({ method: 'POST', url: '/work-moves', payload })).statusCode).toBe(
      400,
    );
    expect(
      (await app.inject({ method: 'POST', url: '/work-moves/operations', payload })).statusCode,
    ).toBe(400);
    expect((await app.inject({ method: 'GET', url: '/work-moves/operations' })).statusCode).toBe(
      400,
    );
    expect(services.registry.work_move_source).not.toHaveBeenCalled();
    principal = OWNER;
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/work-moves',
          payload: { ...payload, override: true },
        })
      ).statusCode,
    ).toBe(400);
    expect((await app.inject({ method: 'GET', url: '/work-moves' })).json()).toEqual({
      move_everything_machine_id: null,
    });
  } finally {
    await app.close();
  }
});

/** Gap: synchronous move coverage cannot prove the browser receives acceptance while
 * source verification waits, or that another screen can recover and follow that work. */
test('browser operation returns before the move, survives reread, rejects duplicate work and retains destination setup phases', async () => {
  const services = setup();
  const { create_operation, read_operations, report_move_setup } = await import('./operations.ts');
  const ticket = create(services.database);
  let release!: (proof: WorkMoveProof) => void;
  const proof = new Promise<WorkMoveProof>((resolve) => {
    release = resolve;
  });
  services.registry.work_move_source.mockImplementation(() => proof);
  const send = services.system.send.bind(services.system);
  vi.spyOn(services.system, 'send').mockImplementation(async (...args) => {
    services.database.write((tx) => report_move_setup(tx, args[0], 'starting_worker', Date.now()));
    return send(...args);
  });
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: fixture_path(services.database),
  });
  const app = Fastify();
  app.decorate('executor_registry', services.registry as unknown as VpsExecutorRegistry);
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  await app.register(routes, {
    database: services.database,
    config,
    clock: buildClock(config),
    conversationSystem: services.system,
    identity: () => ({ principal: OWNER, conversationId: null }),
  });
  try {
    const payload = {
      owner: { kind: 'ticket', id: ticket.id },
      machine_id: ORIGINAL_MAC_MACHINE_ID,
    };
    const response = await app.inject({ method: 'POST', url: '/work-moves/operations', payload });
    expect(response.statusCode).toBe(202);
    const operation_id = response.json().operation_id;
    await vi.waitFor(() => expect(services.registry.work_move_source).toHaveBeenCalled());
    const current = (await app.inject({ method: 'GET', url: '/work-moves/operations' })).json()
      .operations[0];
    expect(current).toMatchObject({
      operation_id,
      finished_at_ms: null,
      items: [{ phase: 'verifying', result: null }],
    });
    expect(
      (await app.inject({ method: 'POST', url: '/work-moves/operations', payload })).statusCode,
    ).toBe(400);
    expect(() =>
      create_operation(
        services,
        ORIGINAL_MAC_MACHINE_ID,
        payload.owner as { kind: 'ticket'; id: string },
      ),
    ).toThrow('already has a move');
    release({ published_branch: 'ticket/published-work', published_revision: 'a'.repeat(40) });
    await vi.waitFor(() =>
      expect(services.database.read(read_operations)[0]!.finished_at_ms).not.toBeNull(),
    );
    const settled = services.database.read(read_operations)[0]!;
    expect(settled.items[0]!.result).toMatchObject({ moved: true, error: null });
    expect(settled.items[0]!.phases.map((phase) => phase.phase)).toEqual([
      'queued',
      'stopping',
      'verifying',
      'recording',
      'recovering',
      'starting',
      'complete',
    ]);
    expect(settled.items[0]!.phases.every((phase) => phase.finished_at_ms !== null)).toBe(true);
  } finally {
    release({ published_branch: null, published_revision: null });
    await app.close();
  }
});

/** Gap: a restart must never strand durable progress or silently repeat relocation. */
test('server interruption settles unfinished items with their actual destination and preserves completed results', async () => {
  const services = setup();
  const { create_operation, interrupt_operations, read_operations, run_operation } =
    await import('./operations.ts');
  const ticket = create(services.database);
  const done = create_operation(services, ORIGINAL_MAC_MACHINE_ID, {
    kind: 'ticket',
    id: ticket.id,
  });
  await run_operation(services, done);
  const pending = create_operation(services, HOME_MACHINE_ID, { kind: 'ticket', id: ticket.id });
  interrupt_operations(services);
  const operations = services.database.read(read_operations);
  expect(
    operations.find((operation) => operation.operation_id === done.operation_id)!.items[0]!.result,
  ).toMatchObject({ moved: true, error: null });
  const interrupted = operations.find(
    (operation) => operation.operation_id === pending.operation_id,
  )!;
  expect(interrupted.finished_at_ms).not.toBeNull();
  expect(interrupted.items[0]!.phase).toBe('failed');
  expect(interrupted.items[0]!.result).toMatchObject({
    moved: false,
    error: expect.stringContaining('server stopped'),
  });
  expect(read(services.database, ticket.id).machine_id).toBe(ORIGINAL_MAC_MACHINE_ID);
});

/** Gap: background work must not continue relocation after producer shutdown seals it. */
test('move lifetime joins its current check and refuses the destination write after shutdown', async () => {
  const services = setup();
  const { create_operation, read_operations, WorkMoveOperationRuntime } =
    await import('./operations.ts');
  const runtime = new WorkMoveOperationRuntime(services.database);
  const ticket = create(services.database);
  let release!: (proof: WorkMoveProof) => void;
  services.registry.work_move_source.mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const operation = create_operation(services, ORIGINAL_MAC_MACHINE_ID, {
    kind: 'ticket',
    id: ticket.id,
  });
  runtime.submit(services, operation);
  await vi.waitFor(() => expect(services.registry.work_move_source).toHaveBeenCalled());
  let settled = false;
  const close = runtime.close({ deadline: performance.now() / 1000 + 5 }).then(() => {
    settled = true;
  });
  await Promise.resolve();
  expect(settled).toBe(false);
  release({ published_branch: null, published_revision: null });
  await close;
  expect(read(services.database, ticket.id).machine_id).toBe(HOME_MACHINE_ID);
  expect(services.database.read(read_operations)[0]!.items[0]!.result).toMatchObject({
    moved: false,
    error: expect.stringContaining('server stopped'),
  });
  expect(() => runtime.assert_open()).toThrow('server stopped');
});
