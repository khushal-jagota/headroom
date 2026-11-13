/** Actual facade and store custody with a scripted native event producer. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { setImmediate as yield_turn } from 'node:timers/promises';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import { BackendPromptAccepted } from '../../../conversation/backends/contracts.ts';
import type {
  BackendChild,
  BackendEventSink,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import { as_object, decode_json, decode_event_arguments } from '../../protocol.ts';
import { normalize_integer } from '../../integers.ts';
import { MacRunner } from '../runner/index.ts';
import { ExecutorPaths } from '../configuration.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import type { OutboxCleanup } from '../storage/resources.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import {
  gate,
  native_fixture_options,
  open_fixture_stores,
  record_commands,
  pending_envelopes,
  close_fixture_owners,
} from './runner_fixture.ts';
import type { FixtureStores } from './runner_fixture.ts';

const started = performance.now(),
  root = process.argv[2]!,
  now = 1_700_000_010_000n;
const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as {
  commands: Record<string, string>;
  events: Record<string, string>;
  before: string[];
  middle: string[];
  after: string[];
};
const commands = Object.fromEntries(
  Object.entries(input.commands).map(([key, raw]) => [key, decode_json(raw, 'ChildCommand')]),
);
const events = Object.fromEntries(
  Object.entries(input.events).map(([key, raw]) => [key, decode_json(raw, 'BackendEvent')]),
);
const options = { deadline: performance.now() / 1000 + 25 };
const owners: OutboxCleanup[] = [],
  failures: Error[] = [],
  live: unknown[] = [],
  states: unknown[] = [];
const on_failure = (error: Error) => {
  failures.push(error);
};
const release = gate(),
  entered = gate(),
  cleanup_release = gate();
let sink!: BackendEventSink,
  native_stops = 0,
  cleanup_native_failure: Error | null = null;
async function emit(key: string) {
  const event = events[key]!;
  await sink.report(
    event.turn as Parameters<BackendEventSink['report']>[0],
    decode_event_arguments(event) as BackendEventValue,
  );
}
const unexpected = async (): Promise<never> => {
  throw new Error('Unexpected native operation.');
};
const native = {
  async start() {},
  async write_prompt() {
    return new BackendPromptAccepted({ composed_content_delivered: true });
  },
  async answer_permission_ask() {},
  async stop() {
    native_stops++;
    if (cleanup_native_failure !== null) {
      const failure = cleanup_native_failure;
      cleanup_native_failure = null;
      throw failure;
    }
    await emit('stream');
  },
  steer: unexpected,
  cancel_running_turn: unexpected,
  answer_user_input: unexpected,
  withdraw_delivery: unexpected,
  detach: unexpected,
  stop_outside_lock: false,
} as unknown as BackendChild;
let parity: unknown,
  lifecycle: Record<string, boolean>,
  cleanup: Record<string, boolean>,
  stores: FixtureStores | undefined;
try {
  stores = await open_fixture_stores(join(root, 'spool'), now, on_failure, options);
  const { store, journal } = stores;
  const runner = new MacRunner({
    incarnation: 'inc',
    journal,
    outbox: store,
    now_ms: () => normalize_integer(now),
    inputs: { download_inputs: unexpected },
    live: { send: (event) => live.push(as_object(event, 'BackendEvent')) },
    new_update_id: () => {
      throw new Error('Updates are outside this event flow.');
    },
    native: native_fixture_options({
      paths: new ExecutorPaths(root),
      workspace_home: root,
      on_failure,
      factory: () => (values) => {
        sink = values.event_sink;
        return native;
      },
      native_skill_home: () => join(root, 'native'),
      snapshot_directory: join(root, 'skills'),
    }),
  });
  owners.unshift(runner);
  await record_commands(
    journal,
    Object.values(commands).map((command, index) => ({
      command_sequence: index + 1,
      command_id: command.command_id,
      created_at_vps_ms: normalize_integer(now),
      child_command: command,
    })),
  );
  async function snapshot() {
    states.push({
      working: runner.working_turns().map((turn) => as_object(turn, 'WorkingTurn')),
      quiescence: as_object(await runner.quiesce(), 'Quiescence'),
      background: [...runner.native_children.get('child')!.background_task_ids].sort(),
    });
    runner.resume_after_cancelled_update();
  }
  await runner.run(commands.start!);
  await runner.run(commands.prompt!);
  for (const key of input.before) await emit(key);
  await snapshot();
  const answer = as_object(await runner.run(commands.permission!), 'CommandOutcome');
  for (const key of input.middle) await emit(key);
  await snapshot();
  for (const key of input.after) await emit(key);
  const source = runner.source_work(commands.start!.child, async () => {
    entered.resolve();
    await release.promise;
    await emit('held');
  });
  await entered.promise;
  await emit('ended');
  await snapshot();
  let stop_finished = false;
  const stopping = runner.run(commands.stop!).finally(() => {
    stop_finished = true;
  });
  void stopping.catch(() => {});
  while (!runner.native_children.is_retiring('child')) {
    assert(!stop_finished);
    await yield_turn();
  }
  await assert.rejects(
    runner.source_work(commands.stop!.child, async () => {}),
    { name: 'PermissionError' },
  );
  const waiting = !stop_finished && native_stops === 0;
  release.resolve();
  await source;
  const stop = as_object(await stopping, 'CommandOutcome');
  const pending = pending_envelopes(store);
  parity = { live: [...live], states, answer, stop, pending, waiting, native_stops };
  assert.deepEqual(failures, []);
  // A second retained producer exercises terminal custody without another host or journal flow.
  const terminal = { ...commands.start!.child, child_id: 'terminal-child' };
  const start = commands.start!.arguments;
  assert(start.kind === 'start');
  await runner.native_children.start(terminal, start, () => {});
  const raw_source = new ShutdownDeadlineExceeded(['scripted source capability']);
  const raw_native = new ShutdownDeadlineExceeded(['scripted native capability']);
  const cleanup_stops = native_stops;
  cleanup_native_failure = raw_native;
  const held_source = runner.source_work(terminal, async () => {
    await cleanup_release.promise;
    throw raw_source;
  });
  void held_source.catch(() => {});
  let actual_settled = false;
  const actual = runner
    .settled()
    .catch((error: unknown) => error)
    .finally(() => {
      actual_settled = true;
    });
  const operation_error = new Error('scripted maintenance operation');
  const pending_count = store.pending_count.bind(store);
  store.pending_count = () => {
    throw operation_error;
  };
  const failed_operation = runner.quiesce().catch((error: unknown) => error);
  const expired = await runner.stop({ deadline: performance.now() / 1000 - 1 }).then(
    () => null,
    (error: unknown) => error,
  );
  try {
    assert.equal(await failed_operation, operation_error);
  } finally {
    store.pending_count = pending_count;
  }
  const retry = runner.stop(options).then(
    () => null,
    (error: unknown) => error,
  );
  await runner.native_children.settled().catch(() => {});
  await yield_turn();
  const actual_waited = !actual_settled;
  cleanup_release.resolve();
  await held_source.catch((error: unknown) => {
    if (error !== raw_source) throw error;
  });
  const outward = await retry;
  const actual_failure = await actual;
  // Actual custody joined. Its intentional diagnostics are asserted before storage teardown.
  owners.splice(owners.indexOf(runner), 1);
  const retains = (error: unknown, expected: unknown): boolean =>
    error === expected ||
    (error instanceof AggregateError &&
      error.errors.some((cause: unknown) => retains(cause, expected))) ||
    (error instanceof Error && error.cause !== undefined && retains(error.cause, expected));
  cleanup = {
    deadline_typed:
      expired instanceof ShutdownDeadlineExceeded && expired instanceof ServerLifecycleError,
    actual_waited,
    raw_source_retained: retains(actual_failure, raw_source),
    raw_operation_retained: retains(actual_failure, operation_error),
    raw_native_retained_after_retry:
      retains(actual_failure, raw_native) && native_stops === cleanup_stops + 2,
    outward_typed: outward instanceof ServerLifecycleError && retains(outward, raw_native),
  };
  assert(Object.values(cleanup).every(Boolean));
  assert.equal(failures.length, 1);
  let touched = false;
  const unread = new Proxy({} as CanonicalReader, {
    get() {
      touched = true;
      throw new Error('Closed runner accessed its replay input.');
    },
  });
  await assert.rejects(runner.run(commands.start!));
  await assert.rejects(runner.publish_owed_outputs(unread, unread));
  await assert.rejects(
    runner.source_work(commands.start!.child, async () => {
      touched = true;
    }),
  );
  await emit('delta');
  lifecycle = {
    permanent: runner.permanently_stopped,
    rejected_before_callbacks: !touched,
    late_live_dropped: live.length === 3,
  };
} catch (error) {
  const cleanup = (error as { cleanup?: OutboxCleanup }).cleanup;
  if (
    cleanup &&
    cleanup !== stores?.store &&
    cleanup !== stores?.journal &&
    !owners.includes(cleanup)
  )
    owners.unshift(cleanup);
  throw error;
} finally {
  release.resolve();
  cleanup_release.resolve();
  if (stores) owners.push(stores.journal, stores.store);
  await close_fixture_owners(owners, options);
}
process.stdout.write(
  stringify_pydantic_json({
    parity,
    lifecycle,
    cleanup,
    elapsed_seconds: (performance.now() - started) / 1000,
  }),
);
