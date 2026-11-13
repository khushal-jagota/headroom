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
import { outcome, owed_outputs } from '../runner/logic/outcomes.ts';
import { MacCommandQueues } from '../mailbox/queues.ts';
import { read_canonical_body } from '../mailbox/poll.ts';
import type { ChildCommand } from '../../wire.generated.ts';
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
  joined_start: string;
  events: Record<string, string>;
  before: string[];
  middle: string[];
  after: string[];
};
const commands = Object.fromEntries(
  Object.entries(input.commands).map(([key, raw]) => [key, decode_json(raw, 'ChildCommand')]),
);
const command_count = Object.keys(commands).length;
const events = Object.fromEntries(
  Object.entries(input.events).map(([key, raw]) => [key, decode_json(raw, 'BackendEvent')]),
);
const options = { deadline: performance.now() / 1000 + 25 };
const owners: OutboxCleanup[] = [],
  failures: Error[] = [],
  live: unknown[] = [],
  states: unknown[] = [];
const on_failure = (error: Error) => failures.push(error);
const debug: unknown[][] = [],
  original_debug = console.debug;
console.debug = (...values: unknown[]) => debug.push(values);
const release = gate(),
  entered = gate(),
  cleanup_release = gate(),
  join_entered = gate(),
  join_release = gate();
const terminal_entered = gate(),
  terminal_release = gate();
const signal = new AbortController().signal;
function mailbox_commands(values: readonly ChildCommand[], first_sequence: number) {
  return values.map((command, index) => ({
    command_sequence: first_sequence + index,
    command_id: command.command_id,
    created_at_vps_ms: normalize_integer(now),
    child_command: command,
  }));
}
function observed_error(task: Promise<unknown>): Promise<unknown> {
  return task.catch((error: unknown) => error);
}
let sink!: BackendEventSink,
  native_stops = 0,
  terminal_start = false,
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
  async start() {
    if (terminal_start) {
      terminal_entered.resolve();
      await terminal_release.promise;
    }
  },
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
    if (terminal_start) terminal_release.resolve();
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
        terminal_start =
          new Map(values.resolved_start.role_materials?.identity_environment_variables).get(
            'PLAN_EXECUTOR_CHILD_ID',
          ) === 'terminal-child';
        return native;
      },
      native_skill_home: () => join(root, 'native'),
      snapshot_directory: join(root, 'skills'),
    }),
  });
  owners.unshift(runner);
  await record_commands(journal, mailbox_commands(Object.values(commands), 1));
  async function snapshot() {
    states.push({
      working: runner.working_turns().map((turn) => as_object(turn, 'WorkingTurn')),
      quiescence: as_object(await runner.quiesce(signal), 'Quiescence'),
      background: [...runner.native_children.get('child')!.background_task_ids].sort(),
    });
    runner.resume_after_cancelled_update();
  }
  await runner.run(commands.start!, signal);
  await runner.run(commands.prompt!, signal);
  for (const key of input.before) await emit(key);
  await snapshot();
  const answer = as_object(await runner.run(commands.permission!, signal), 'CommandOutcome');
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
  const stopping = runner.run(commands.stop!, signal).finally(() => {
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
  let touched = false;
  const unread = new Proxy({} as CanonicalReader, {
    get() {
      touched = true;
      throw new Error('Runner accessed rejected input.');
    },
  });
  const aborted = new AbortController();
  aborted.abort();
  await assert.rejects(runner.run(unread as unknown as ChildCommand, aborted.signal), {
    name: 'AbortError',
  });
  assert(!touched);
  // This accepted deviation keeps failed-join uncertainty while still cleaning the native child.
  const joined_start = decode_json(input.joined_start, 'ChildCommand');
  const joined_stop = { ...commands.stop!, child: joined_start.child, command_id: 'joined-stop' };
  const joined_retry = { ...joined_stop, command_id: 'joined-stop-retry' };
  await record_commands(
    journal,
    mailbox_commands([joined_start, joined_stop, joined_retry], command_count + 1),
  );
  const original_finish = journal.finish.bind(journal),
    join_failure = new Error('scripted joined journal failure');
  let reject_finish = true;
  journal.finish = async (id, body, owed) => {
    if (id === joined_start.command_id && reject_finish) {
      reject_finish = false;
      join_entered.resolve();
      await join_release.promise;
      throw join_failure;
    }
    await original_finish(id, body, owed);
  };
  const failed_command = runner.run(joined_start, signal);
  void failed_command.catch(() => {});
  await join_entered.promise;
  const stops_before_join = native_stops;
  const failed_stop = runner.run(joined_stop, signal);
  while (!runner.native_children.is_retiring(joined_start.child.child_id)) await yield_turn();
  join_release.resolve();
  assert.equal(await observed_error(failed_command), join_failure);
  const uncertain_stop = await failed_stop;
  const cleaned_after_failure = native_stops === stops_before_join + 1;
  await runner.run(joined_start, signal); // Finish known durable work without invoking native start again.
  const retry_stop = await runner.run(joined_retry, signal);
  journal.finish = original_finish;
  const joined_cleanup =
    [uncertain_stop, retry_stop].every(
      (result) =>
        result.disposition === 'uncertain' &&
        result.result.error_type === 'Error' &&
        result.result.error_message === join_failure.message,
    ) &&
    cleaned_after_failure &&
    native_stops === stops_before_join + 1 &&
    [joined_stop, joined_retry].every((command) =>
      owed_outputs(command, outcome(command, 'succeeded', {})).every(
        (owed) => store.lookup(owed.submission_id) === null,
      ),
    );
  const terminal = { ...commands.start!.child, child_id: 'terminal-child' };
  const terminal_command = { ...commands.start!, child: terminal, command_id: 'terminal-start' };
  const terminal_mailbox = mailbox_commands([terminal_command], command_count + 4)[0]!;
  await record_commands(journal, [terminal_mailbox]);
  const queue_failures: Error[] = [];
  const queues = new MacCommandQueues({
    runner,
    journal,
    catalog: { read_result: unexpected },
    result_finished() {},
    on_failure: (error) => queue_failures.push(error),
  });
  owners.unshift(queues);
  queues.submit(terminal_mailbox);
  await terminal_entered.promise;
  let queue_settled = false;
  const queue_actual = observed_error(queues.settled()).finally(() => {
    queue_settled = true;
  });
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
  const actual = observed_error(runner.settled()).finally(() => {
    actual_settled = true;
  });
  const operation_error = new Error('scripted maintenance operation');
  const pending_count = store.pending_count.bind(store);
  store.pending_count = () => {
    throw operation_error;
  };
  const failed_operation = observed_error(runner.quiesce(signal));
  const expired = await observed_error(queues.stop({ deadline: performance.now() / 1000 - 1 }));
  try {
    assert((await failed_operation) instanceof ShutdownDeadlineExceeded);
  } finally {
    store.pending_count = pending_count;
  }
  while (failures.length === 0) await yield_turn();
  const retry = observed_error(runner.stop(options));
  await runner.native_children.settled().catch(() => {});
  await yield_turn();
  const actual_waited = !actual_settled && !queue_settled;
  cleanup_release.resolve();
  assert.equal(await observed_error(held_source), raw_source);
  const outward = await retry;
  const actual_failure = await actual;
  const queue_failure = await queue_actual;
  owners.splice(owners.indexOf(queues), 1);
  const terminal_result = await journal.result_of(terminal_command.command_id);
  assert(terminal_result);
  const terminal_outcome = decode_json(
    await read_canonical_body(terminal_result),
    'CommandResult',
  ).child_outcome;
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
    queue_actual_joined:
      queue_settled &&
      queue_failure === undefined &&
      terminal_outcome?.disposition === 'succeeded' &&
      !runner.native_children.get(terminal.child_id)?.lock.locked,
  };
  assert(Object.values(cleanup).every(Boolean));
  assert.equal(failures.length, 1);
  assert.deepEqual([queue_failures, debug.length], [[], 1]);
  assert.equal(debug[0]![0], 'Mac command canceled during stop command=terminal-start:');
  await assert.rejects(runner.run(commands.start!, signal));
  await assert.rejects(runner.publish_owed_outputs(unread, unread));
  await assert.rejects(
    runner.source_work(commands.start!.child, async () => {
      touched = true;
    }),
  );
  await emit('delta');
  lifecycle = {
    joined_failed_command_cleanup: joined_cleanup,
    permanent: runner.permanently_stopped,
    rejected_before_callbacks: !touched,
    late_live_dropped: live.length === 3,
  };
} catch (error) {
  const cleanup = (error as { cleanup?: OutboxCleanup }).cleanup;
  if (cleanup && ![stores?.store, stores?.journal, ...owners].includes(cleanup))
    owners.unshift(cleanup);
  throw error;
} finally {
  console.debug = original_debug;
  release.resolve();
  cleanup_release.resolve();
  join_release.resolve();
  terminal_release.resolve();
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
