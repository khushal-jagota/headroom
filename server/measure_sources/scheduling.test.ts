/** Gap: route admission cannot prove concurrent execution ownership or shutdown drains.
 * Reuse the shared Ticket schema and a deferred runtime, without a browser or child host.
 */
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { OWNER } from '../core/contracts.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import type { OwnedAreaContext } from '../core/authority/register.ts';
import type { RunAdmission, RunResult, SourceRuntime } from './contracts.ts';
import { failure } from './contracts.ts';
import { create_source, submit_script } from './actions.ts';
import { request_run, claim_run } from './run_actions.ts';
import { read_run } from './data.ts';
import { SourceDispatcher } from './dispatcher.ts';
import { current_source_slot } from './scheduling.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
const turn = () => new Promise<void>((resolve) => setImmediate(resolve));
const result = (interrupted = false): RunResult => ({
  observations: [],
  dataset_results: [],
  failure: interrupted ? failure('interrupted') : null,
  metrics: {
    request_count: 0,
    response_bytes: 0,
    child_output_bytes: 0,
    elapsed_milliseconds: 0,
    script_cpu_milliseconds: 0,
  },
});
test('two source executions retain per-source exclusion and drain cancellation before ownership ends', async () => {
  const changes = new ChangeSignal();
  const database = fixture({ onChange: () => changes.emit() });
  const path = fixture_path(database);
  const clock = new TestClock('2027-01-01T08:30:00+14:00');
  const context: OwnedAreaContext = {
    database,
    changes,
    clock,
    config: loadConfig('/missing-unit13-config', { PLAN_DB_PATH: path, PLAN_TEST_MODE: '1' }),
    assert_database_owned() {
      if (!owned) throw new Error('ownership ended');
    },
  };
  let owned = true;
  const pending = new Map<string, (result: RunResult) => void>();
  const cancelled = new Set<string>();
  const runtime: SourceRuntime = {
    execute(admission: RunAdmission) {
      return new Promise<RunResult>((resolve) => {
        if (cancelled.has(admission.run.id)) resolve(result(true));
        else pending.set(admission.run.id, resolve);
      });
    },
    cancel(id) {
      cancelled.add(id);
      pending.get(id)?.(result(true));
    },
  };
  const source = (name: string) =>
    database.write((sql) => {
      const created = create_source(
        sql,
        {
          name,
          scope: { project_id: 'project_other', account: name },
          responsible: { kind: 'chief', id: 'chief' },
        },
        { principal: OWNER, now: clock.nowUnix() },
      );
      return submit_script(
        sql,
        created.id,
        { text: '// deferred test runtime', expected_revision: 1 },
        { principal: OWNER, now: clock.nowUnix() },
      );
    });
  const first = source('first'),
    second = source('second');
  const enqueue = (id: string) =>
    database.write((sql) =>
      request_run(sql, id, {
        mode: 'test',
        history: null,
        expected_script_revision: 1,
        principal: OWNER,
        now: clock.nowUnix(),
      }),
    );
  const a = enqueue(first.id),
    b = enqueue(second.id),
    blocked = enqueue(first.id);
  const dispatcher = new SourceDispatcher({ context, runtime });
  dispatcher.start();
  expect(current_source_slot(clock)).toEqual(['2027-01-01', '08:30']);
  expect(dispatcher.poll_once()).toEqual([a.id, b.id]);
  await turn();
  expect(pending.size).toBe(2);
  expect(dispatcher.poll_once()).toEqual([]);
  expect(database.read((sql) => read_run(sql, blocked.id)).status).toBe('pending');
  pending.get(a.id)!(result());
  await turn();
  expect(database.read((sql) => read_run(sql, a.id)).status).toBe('succeeded');
  dispatcher.poll_once();
  await turn();
  await dispatcher.stop({ deadline: performance.now() / 1000 + 5 });
  expect(database.read((sql) => read_run(sql, b.id)).status).toBe('interrupted');
  expect(database.read((sql) => read_run(sql, blocked.id)).status).toBe('interrupted');
  // A runtime that ignores cancellation reaches the same shared deadline. The
  // lifecycle retains ownership on this failure, so its final write remains legal.
  const late = enqueue(first.id);
  let finishLate!: (value: RunResult) => void;
  const uncooperative = new SourceDispatcher({
    context,
    runtime: {
      execute: () =>
        new Promise<RunResult>((resolve) => {
          finishLate = resolve;
        }),
      cancel() {},
    },
  });
  uncooperative.start();
  uncooperative.poll_once();
  await turn();
  let settled = false;
  try {
    await expect(uncooperative.stop({ deadline: performance.now() / 1000 })).rejects.toBeInstanceOf(
      ShutdownDeadlineExceeded,
    );
    const actual = uncooperative.settled().then(() => {
      settled = true;
    });
    await turn();
    expect(settled).toBe(false);
    expect(owned).toBe(true);
    expect(database.read((sql) => read_run(sql, late.id)).status).toBe('running');
    finishLate(result(true));
    await actual;
    expect(database.read((sql) => read_run(sql, late.id)).status).toBe('interrupted');
  } finally {
    finishLate(result(true));
    await uncooperative.settled();
  }
  owned = false;
  await turn();
});

test('a new dispatcher interrupts an abandoned run before it admits another execution', async () => {
  const database = fixture();
  const clock = new TestClock('2027-01-01T08:30:00+14:00');
  const source = database.write((sql) =>
    create_source(
      sql,
      {
        name: 'abandoned',
        scope: { project_id: 'project_other', account: 'abandoned' },
        responsible: { kind: 'chief', id: 'chief' },
      },
      { principal: OWNER, now: clock.nowUnix() },
    ),
  );
  database.write((sql) =>
    submit_script(
      sql,
      source.id,
      { expected_revision: 1, text: '// never executed' },
      { principal: OWNER, now: clock.nowUnix() },
    ),
  );
  const run = database.write((sql) =>
    request_run(sql, source.id, {
      mode: 'test',
      history: null,
      expected_script_revision: 1,
      principal: OWNER,
      now: clock.nowUnix(),
    }),
  );
  database.write((sql) => claim_run(sql, run.id, { now: clock.nowUnix() }));
  const dispatcher = new SourceDispatcher({
    context: {
      database,
      clock,
      changes: new ChangeSignal(),
      config: loadConfig('/missing-unit13-config', {
        PLAN_DB_PATH: fixture_path(database),
        PLAN_TEST_MODE: '1',
      }),
      assert_database_owned() {},
    },
    runtime: {
      execute: async () => {
        throw new Error('no abandoned execution');
      },
      cancel() {},
    },
  });
  dispatcher.start();
  expect(database.read((sql) => read_run(sql, run.id)).status).toBe('interrupted');
  await dispatcher.stop({ deadline: performance.now() / 1000 + 5 });
});

/** Gap: the dispatcher tests do not cross production composition admission. */
test('source clock is off by default and shares dispatcher ownership and the stop deadline', async () => {
  const { compose_background_services } = await import('../lifecycle/background.ts');
  const { SourceScheduleLoop } = await import('./schedule_loop.ts');
  const database = fixture();
  const context: OwnedAreaContext = {
    database,
    changes: new ChangeSignal(),
    clock: new TestClock('2027-01-01T08:30:00+14:00'),
    config: loadConfig('/missing-unit13-config', {
      PLAN_DB_PATH: fixture_path(database),
      PLAN_TEST_MODE: '0',
      PLAN_DISPATCH_ENABLED: 'true',
    }),
    assert_database_owned() {},
  };
  const start = vi.spyOn(SourceScheduleLoop.prototype, 'start');
  const stop = vi.spyOn(SourceScheduleLoop.prototype, 'stop');
  try {
    const deadline = performance.now() / 1000 + 5;
    for (const options of [
      { dispatcher_owned: true },
      { dispatcher_owned: false, areaLoops: { measureSources: true } },
    ]) {
      const inert = compose_background_services(context, options);
      await inert.start();
      await inert.close({ deadline });
    }
    expect(start).not.toHaveBeenCalled();
    const admitted = compose_background_services(context, {
      dispatcher_owned: true,
      areaLoops: { measureSources: true },
    });
    await admitted.start();
    expect(start).toHaveBeenCalledTimes(1);
    await admitted.close({ deadline });
    expect(stop).toHaveBeenCalledWith({ deadline });
  } finally {
    start.mockRestore();
    stop.mockRestore();
  }
});
