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
import { WORK_MOVE_PROTOCOL_VERSION } from '../execution_configuration.ts';
import { read_manager } from '../managers/data.ts';
import {
  issue_launch_token,
  authenticate_launch_token,
  canonical_principal,
} from '../agent_identity/data.ts';
import { move_work, move_everything } from './service.ts';
import { agent_machine_id } from '../machines/conversation_machine.ts';
import { work_move_status, unfinished_work } from './data.ts';
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
      "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.protocol_version',?) WHERE machine_id=?",
      [WORK_MOVE_PROTOCOL_VERSION - 1, ORIGINAL_MAC_MACHINE_ID],
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
      "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.protocol_version',?) WHERE machine_id=?",
      [WORK_MOVE_PROTOCOL_VERSION, ORIGINAL_MAC_MACHINE_ID],
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
