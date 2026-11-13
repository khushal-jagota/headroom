/** Gaps: autonomous orchestration and delivery fates are absent from Ticket route proofs.
 * Reuses Ticket SQLite fixtures and the existing in-memory contract. No provider adapter.
 */
import { afterEach, expect, test, vi } from 'vitest';
import {
  fixture,
  fixture_path,
  create as create_ticket,
  edit,
  read,
  now,
  claim,
  propose,
} from './ticket_fixture.ts';
import * as data from '../tickets/data/index.ts';
import * as decisions from '../tickets/decision_messages.ts';
import { restart_worker } from '../tickets/worker_restart.ts';
import { OWNER } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import {
  start_ready_worker_step,
  WorkerStepReadinessLoop,
} from '../runtime/worker_step_readiness_loop.ts';
import { prepare_background_loops } from '../core/loops.ts';
import Fastify from 'fastify';
import { dirname } from 'node:path';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import { readinessTestRoutes } from '../runtime/testing.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { acquireLock } from '../core/locks/index.ts';
import { ChangeSignal } from '../core/changes/index.ts';
afterEach(() => vi.restoreAllMocks());
// Ordinary-step cases start after the initial approval instruction reached its Worker.
function create(...args: Parameters<typeof create_ticket>): ReturnType<typeof create_ticket> {
  const ticket = create_ticket(...args);
  args[0].write((sql) =>
    sql.run("UPDATE ticket_decision_messages SET delivery_status='delivered' WHERE ticket_id=?", [
      ticket.id,
    ]),
  );
  return ticket;
}
function setup() {
  const db = fixture();
  configure_conversation_start(db, {
    db_path: fixture_path(db),
    workspace_root: dirname(fixture_path(db)),
  });
  const system = new InMemoryConversationSystem();
  const options = {
    connect_database: () => db,
    conversation_system: system,
    worker_type_registry: configured_worker_type_registry(),
    now: () => now,
  };
  return { db, system, options };
}

// Python restart ports: the existing sequential HTTP list cannot interleave a
// Worker proposal or replacement claim with an awaited kill, or race the link lock.
test('restart keeps a worker completion that lands during kill', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const old_id = read(db, ticket.id).conversation_id!;
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    propose(db, read(db, ticket.id), 'Completed while restart waited.');
    await kill(id);
  });
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.started).toBe(false);
  expect(result.not_started_because).toContain('awaiting_approval');
  expect(read(db, ticket.id)).toMatchObject({
    worker_step_claim: 'none',
    conversation_id: null,
    pending_proposal: { body: 'Completed while restart waited.' },
  });
  expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
});

test('restart releases the current errored claim generation after kill', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const claimed = read(db, ticket.id);
  db.write((tx) =>
    data.mark_worker_step_claim_errored(tx, ticket.id, {
      expected_claim: claimed.worker_step_claim,
      expected_claim_revision: claimed.worker_step_claim_revision,
      now,
      error_reason: 'delivery uncertain',
    }),
  );
  const admitted = read(db, ticket.id);
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    db.write((tx) => {
      expect(
        data.release_worker_step_claim(tx, ticket.id, {
          expected_claim: admitted.worker_step_claim,
          expected_claim_revision: admitted.worker_step_claim_revision,
          now,
        }),
      ).toBe(true);
      data.mark_ticket_errored(tx, ticket.id, { now, error_reason: 'delivery uncertain' });
    });
    await kill(id);
  });
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.started).toBe(true);
  const restarted = read(db, ticket.id);
  expect(restarted.worker_step_claim).toBe('out');
  expect(restarted.worker_step_claim_revision).toBeGreaterThan(admitted.worker_step_claim_revision);
  expect(restarted.conversation_id).not.toBe(claimed.conversation_id);
});

test('the next opener waits for restart and only one starts a fresh conversation', async () => {
  const { db, system, options } = setup();
  const ticket = edit(db, create(db), { ceiling: 'needs_implementation' });
  await start_ready_worker_step(ticket.id, options);
  const old_id = read(db, ticket.id).conversation_id!;
  let announce_kill!: () => void;
  const kill_started = new Promise<void>((resolve) => (announce_kill = resolve));
  let release_kill!: () => void;
  const may_kill = new Promise<void>((resolve) => (release_kill = resolve));
  const kill = system.kill.bind(system);
  vi.spyOn(system, 'kill').mockImplementation(async (id) => {
    announce_kill();
    await may_kill;
    await kill(id);
  });
  const restarting = restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  await kill_started;
  try {
    propose(db, read(db, ticket.id), 'Advance before the next opener.');
    let next_settled = false;
    const next_step = start_ready_worker_step(ticket.id, options).then((result) => {
      next_settled = true;
      return result;
    });
    await new Promise((resolve) => setImmediate(resolve));
    expect(next_settled).toBe(false);
    expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
    release_kill();
    const [restarted, next] = await Promise.all([restarting, next_step]);
    expect([restarted.started, next.started].filter(Boolean)).toHaveLength(1);
    const current = read(db, ticket.id);
    expect(current.stage).toBe('needs_implementation');
    expect(current.worker_step_claim).toBe('out');
    expect(current.conversation_id).not.toBeNull();
    expect(current.conversation_id).not.toBe(old_id);
    expect(system.backend_prompt_writes(old_id)).toHaveLength(1);
  } finally {
    release_kill();
    await restarting;
  }
});

// The HTTP restart list never reaches an absent Mac. Admission must not kill,
// start, edit launch configuration, or change its already-owned claim/link.
test('restart refuses a Mac ticket while the Mac is away without changing it', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  db.write((tx) => {
    tx.run("UPDATE tickets SET locale='macos' WHERE id=?", [ticket.id]);
  });
  const before = read(db, ticket.id);
  const kill = vi.spyOn(system, 'kill');
  const start = vi.fn(() => start_ready_worker_step(ticket.id, options));
  const configure = vi.fn();
  await expect(
    restart_worker(system, db, OWNER, ticket.id, {
      now: options.now,
      start_worker_step: start,
      write_employee_configuration: configure,
    }),
  ).rejects.toMatchObject({
    code: 'validation',
    message: 'Waiting for the Mac: restart this Ticket once it is back',
    detail: { ticket_id: ticket.id },
  });
  expect(kill).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  expect(configure).not.toHaveBeenCalled();
  expect(read(db, ticket.id)).toEqual(before);
});
test.each([
  ['refused', () => new PromptDeliveryRefused({ refusal_reason: 'session_did_not_load' }), 'none'],
  ['suppressed', () => new PromptDeliverySuppressed({ reason: 'test suppression' }), 'none'],
  ['uncertain', () => new PromptDeliveryUncertain(), 'errored'],
  [
    'backend_did_not_start',
    () => new PromptDeliveryRefused({ refusal_reason: 'backend_did_not_start' }),
    'errored',
  ],
] as const)('delivery %s preserves its claim outcome', async (_name, fate, expected_claim) => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'send').mockResolvedValue(fate());
  const result = await start_ready_worker_step(ticket.id, options);
  expect(result.started).toBe(false);
  expect(read(db, ticket.id).worker_step_claim).toBe(expected_claim);
  if (_name === 'backend_did_not_start') expect(read(db, ticket.id).conversation_id).not.toBeNull();
});
test('an occupied conversation prevents another claim or opener', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const taken = read(db, ticket.id);
  db.write((tx) =>
    data.release_worker_step_claim(tx, ticket.id, {
      expected_claim: taken.worker_step_claim,
      expected_claim_revision: taken.worker_step_claim_revision,
      now,
    }),
  );
  expect(await system.is_running(taken.conversation_id!)).toBe(true);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  expect(system.backend_prompt_writes(taken.conversation_id!)).toHaveLength(1);
});
test('start refusal releases a new link and the claim', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'start_conversation').mockRejectedValue(new Error('start refused'));
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id)).toMatchObject({ worker_step_claim: 'none', conversation_id: null });
});
test('late uncertainty cannot overwrite a newer claim', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  vi.spyOn(system, 'send').mockImplementation(async () => {
    const taken = read(db, ticket.id);
    db.write((tx) =>
      data.release_worker_step_claim(tx, ticket.id, {
        expected_claim: taken.worker_step_claim,
        expected_claim_revision: taken.worker_step_claim_revision,
        now,
      }),
    );
    claim(db, ticket.id);
    return new PromptDeliveryUncertain();
  });
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id).worker_step_claim).toBe('out');
});
test('a refused user-stage opener forgets its opener and permits a retry', async () => {
  const { db, system, options } = setup();
  const ticket = create(db, { worker_type: 'new_worker' });
  vi.spyOn(system, 'send').mockResolvedValue(
    new PromptDeliveryRefused({ refusal_reason: 'backend_did_not_start' }),
  );
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  expect(
    db.read((sql) =>
      sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=?', [ticket.id]),
    ),
  ).toBeUndefined();
});
test('decisions reach the actual prompt and queue ownership prevents replay', async () => {
  const { db, system, options } = setup();
  const ticket = create_ticket(db);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  const id = read(db, ticket.id).conversation_id!;
  expect(system.backend_prompt_writes(id)[0]).toMatchObject({
    content: expect.arrayContaining([
      expect.objectContaining({
        text: expect.stringContaining('User approved the Brief proposal.'),
      }),
    ]),
  });
  const proposed = propose(db, read(db, ticket.id), 'Revise this proposal.');
  db.write((tx) =>
    data.reject_proposal(tx, ticket.id, {
      message: 'Keep the note.',
      principal: OWNER,
      expected_proposal_revision: proposed.pending_proposal_revision,
      decision_id: 'test-reject',
      now,
    }),
  );
  await start_ready_worker_step(ticket.id, options);
  await start_ready_worker_step(ticket.id, options);
  expect(await system.held_prompts(id)).toHaveLength(1);
  expect((await system.held_prompts(id))[0]!.content).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        text: 'User sent back the What Changes proposal with this note:\nKeep the note.',
      }),
    ]),
  );
  expect(db.read((sql) => decisions.has_undelivered(sql, ticket.id))).toBe(true);
  // Without a queue item or durable outcome, only explicit restart authorizes retry.
  vi.spyOn(system, 'held_prompts').mockResolvedValueOnce([]);
  await start_ready_worker_step(ticket.id, options);
  const status = () =>
    db.read((sql) =>
      sql.get<{ delivery_status: string }>(
        "SELECT delivery_status FROM ticket_decision_messages WHERE decision_id='test-reject'",
      ),
    )!.delivery_status;
  expect(status()).toBe('uncertain');
  expect(read(db, ticket.id).worker_step_claim).toBe('errored');
  await start_ready_worker_step(ticket.id, options);
  expect(status()).toBe('uncertain');
  const result = await restart_worker(system, db, OWNER, ticket.id, {
    now: options.now,
    start_worker_step: () => start_ready_worker_step(ticket.id, options),
  });
  expect(result.revision_restarted_in_fresh_conversation).toBe(true);
  expect(status()).toBe('delivered');
  const fresh = read(db, ticket.id).conversation_id!;
  expect(fresh).not.toBe(id);
  expect(system.backend_prompt_writes(fresh)[0]!.text).toContain(
    'User sent back the What Changes proposal with this note:\nKeep the note.',
  );
});
test('successful user-stage opener releases its claim and cannot run twice', async () => {
  const { db, system, options } = setup();
  const ticket = create(db, { worker_type: 'new_worker' });
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  system.complete_running_turn(read(db, ticket.id).conversation_id!);
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
});
test('poll hook, in-flight deduplication, and shutdown share one loop owner', async () => {
  const { db, options } = setup();
  const ticket = create(db);
  let held = true;
  const loop = new WorkerStepReadinessLoop({ ...options, before_poll: () => held });
  expect(loop.poll_once()).toEqual([]);
  held = false;
  expect(loop.poll_once()).toEqual([ticket.id]);
  expect(loop.poll_once()).toEqual([]);
  await loop.drain();
  expect(loop.poll_once()).toEqual([]);
  await loop.stop({ deadline: performance.now() / 1000 + 1 });
  expect(loop.poll_once()).toEqual([]);
});
test('production composition requires opt-in and dispatcher ownership; test mode never polls automatically', async () => {
  const { db, options } = setup();
  create(db);
  const lock_path = fixture_path(db) + '.dispatcher.lock';
  const owner = acquireLock(lock_path);
  expect(owner).not.toBeNull();
  const contender = acquireLock(lock_path);
  expect(contender).toBeNull();
  const base = {
    enable_worker_readiness: true,
    test_mode: false,
    dispatch_enabled: true,
    dispatcher_owned: owner !== null,
    worker: options,
  };
  expect(
    prepare_background_loops({ ...base, dispatcher_owned: contender !== null })
      .worker_step_readiness_loop,
  ).toBeNull();
  for (const refusal of [
    { enable_worker_readiness: false },
    { test_mode: true },
    { dispatch_enabled: false },
    { dispatcher_owned: false },
  ]) {
    expect(prepare_background_loops({ ...base, ...refusal }).worker_step_readiness_loop).toBeNull();
  }
  const loops = prepare_background_loops(base);
  const changes = new ChangeSignal();
  loops.start(60, changes);
  await loops.worker_step_readiness_loop!.drain();
  const next = create(db);
  changes.emit();
  await new Promise((resolve) => setImmediate(resolve));
  await loops.worker_step_readiness_loop!.drain();
  expect(read(db, next.id).worker_step_claim).toBe('out');
  await loops.stop({ deadline: performance.now() / 1000 + 1 });
  const last = create(db);
  changes.emit();
  await new Promise((resolve) => setImmediate(resolve));
  expect(read(db, last.id).worker_step_claim).toBe('none');
  owner!.close();
});

// Gap: composition admission alone cannot prove that the test HTTP door refuses a missing fake.
test('the readiness test route refuses a missing test conversation system', async () => {
  const db = fixture();
  const ticket = create(db);
  const app = Fastify();
  let stop: { deadline: number } | undefined;
  await app.register(
    readinessTestRoutes(() => {
      if (!stop) throw new Error('Isolated readiness host has not begun shutdown');
      return stop;
    }).routes,
    {
      database: db,
      config: loadConfig('/missing-worker-test-config', { PLAN_TEST_MODE: '1' }),
      clock: new TestClock('2026-07-04T10:00:00+00:00'),
      identity: () => ({ principal: OWNER, conversationId: null }),
    },
  );
  try {
    const result = await app.inject({ method: 'POST', url: '/readiness' });
    expect(result.statusCode).toBe(400);
    expect(result.json().message).toContain('require a test conversation system');
    expect(read(db, ticket.id).worker_step_claim).toBe('none');
  } finally {
    stop = { deadline: performance.now() / 1000 + 1 };
    await app.close();
  }
});

// Gap: the in-memory instrument writes no durable compaction events. Seed only the
// record facts that the scanner reads, then prove its real queue boundary twice.
test('an unanswered compaction sends one queued notice and an uncertain notice prevents another automatic attempt', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  await start_ready_worker_step(ticket.id, options);
  const id = read(db, ticket.id).conversation_id!;
  db.write((tx) => {
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,1,?,?,?)',
      [id, 'prompt', JSON.stringify({ sender_message_id: 'worker_step_message_original' }), now],
    );
    tx.run(
      'UPDATE conversations SET automatically_compacted_through_sequence=2 WHERE conversation_id=?',
      [id],
    );
  });
  const loop = new WorkerStepReadinessLoop(options);
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([ticket.id]);
  await loop.drain();
  expect(await system.held_prompts(id)).toHaveLength(1);
  expect((await system.held_prompts(id))[0]!.content).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ text: expect.stringContaining('Your context was compacted') }),
    ]),
  );
  loop.tell_every_worker_that_lost_its_memory();
  await loop.drain();
  expect(await system.held_prompts(id)).toHaveLength(1);
  system.complete_running_turn(id);
  for (const held of await system.held_prompts(id))
    await system.discard_held_prompt(id, held.held_prompt_id);
  db.write((tx) =>
    tx.run(
      'INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES (?,3,?,?,?)',
      [
        id,
        'prompt_delivery_uncertain',
        JSON.stringify({ sender_message_id: 'worker_step_memory_notice_uncertain' }),
        now,
      ],
    ),
  );
  expect(loop.tell_every_worker_that_lost_its_memory()).toEqual([]);
  await loop.stop({ deadline: performance.now() / 1000 + 1 });
});

// Gap: normal drain cases cannot prove that a stalled provider permits loop shutdown.
test('loop stop aborts its observer immediately and retains raw start ownership past the deadline', async () => {
  const { db, system, options } = setup();
  const ticket = create(db);
  const path = fixture_path(db) + '.hung-dispatcher.lock';
  const owner = acquireLock(path)!;
  let entered!: () => void;
  const sending = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const provider = new Deferred<PromptDeliveryStarted>();
  vi.spyOn(system, 'send').mockImplementation(() => {
    entered();
    return provider.promise;
  });
  const loop = new WorkerStepReadinessLoop(options);
  loop.poll_once();
  try {
    await sending;
    const departure = read(db, ticket.id);
    const abort = vi.spyOn(AbortController.prototype, 'abort');
    const stopping = loop.stop({ deadline: performance.now() / 1000 + 0.01 });
    expect(abort).toHaveBeenCalledOnce();
    await expect(stopping).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    expect(acquireLock(path)).toBeNull();
    let actual_done = false;
    const actual = loop.settled().then(() => {
      actual_done = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(actual_done).toBe(false);
    provider.resolve(new PromptDeliveryStarted());
    await actual;
    owner.close();
    const next = acquireLock(path);
    expect(next).not.toBeNull();
    next!.close();
    expect(read(db, ticket.id)).toMatchObject({
      conversation_id: departure.conversation_id,
      worker_step_claim: 'out',
      worker_step_claim_revision: departure.worker_step_claim_revision,
    });
    expect(loop.poll_once()).toEqual([]);
  } finally {
    provider.resolve(new PromptDeliveryStarted());
    await loop.stop({ deadline: performance.now() / 1000 + 1 }).catch((error: unknown) => {
      if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
    });
    await loop.settled();
    owner.close();
  }
});
