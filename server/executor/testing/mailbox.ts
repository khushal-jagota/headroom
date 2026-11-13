/** Fixed controls over one real mailbox and application database, without a host. */
import { createInterface } from 'node:readline';
import { getEventListeners } from 'node:events';
import type { Database, SqlValue } from '../../core/database/contracts.ts';
import { createDatabase, createReadAccess } from '../../core/database/index.ts';
import { JsonNumber, numeric_value, stringify_python_json } from '../../core/http/numeric_json.ts';
import { SqliteCommandMailbox } from '../vps_mailbox.ts';
import { encode_value, encode_bounded } from '../protocol.ts';
import { parse_executor_json } from '../protocol_json.ts';
import { is_shutdown_business_cancellation } from '../../conversation/shutdown.ts';
import type { StallSource } from '../../stalls/contracts.ts';
import * as data from '../data/mailbox.ts';
import type { WireInteger, WireTypes } from '../wire.generated.ts';

type Statement = { sql: string; values: SqlValue[] };
type Action = {
  op: string;
  path: string;
  now: number;
  method: string;
  args: unknown[];
  key: string;
  statements: Statement[];
  before_sql?: Statement[];
  after_abort?: boolean;
  after_put?: WireTypes['ChildCommand'];
  throw_on: string | null;
  child: WireTypes['ChildReference'];
  turn: WireInteger;
  deadline_ms?: number;
  command: WireTypes['ChildCommand'];
  ids: string[];
};
type Outcome =
  { ok: true; value: unknown } | { ok: false; type: string; message: string; errors?: Outcome[] };
type Held = { promise: Promise<Outcome>; done: boolean };
let database: Database,
  mailbox: SqliteCommandMailbox,
  now = 0,
  signals = 0;
let reads = 0,
  writes = 0,
  writer_ms = 0;
let stalls: StallSource;
let before_sql: Statement[] | undefined,
  after_abort = false;
let observer: AbortController | undefined;
let after_put: WireTypes['ChildCommand'] | undefined, reentered: Promise<unknown> | undefined;
let trace: unknown[] = [],
  throw_on: string | null = null;
const held = new Map<string, Held>();
const scalar = (value: unknown): number => {
  const result = numeric_value(value);
  if (result === undefined) throw new TypeError('A control number is required');
  return result;
};
const parameter = (value: SqlValue): SqlValue =>
  value instanceof JsonNumber ? (value.integer ?? value.value) : value;
function sql(statements: Statement[]): void {
  database.write((tx) => {
    for (const statement of statements) tx.run(statement.sql, statement.values.map(parameter));
  });
}
const facade: Database = {
  read(work) {
    reads++;
    return database.read(work);
  },
  write(work, options) {
    writes++;
    if (before_sql) {
      const statements = before_sql;
      before_sql = undefined;
      sql(statements);
    }
    const nested = after_put,
      abort = after_abort,
      controller = observer;
    after_put = undefined;
    after_abort = false;
    const began = performance.now();
    let result;
    try {
      result = database.write(work, options);
    } finally {
      writer_ms += performance.now() - began;
    }
    if (nested) reentered = mailbox.put_child_command(nested);
    if (abort) controller!.abort(new DOMException('fixture observer cancelled', 'AbortError'));
    return result;
  },
  close() {
    throw new Error('The mailbox cannot close its supplied database');
  },
};
function owner(): void {
  mailbox = new SqliteCommandMailbox(facade, { clock: () => now });
  stalls = mailbox.stall_source();
  mailbox.settled_listener = (command, result) => {
    trace.push({ id: command.command_id, result: encode_value(result, 'CommandResult') });
    if (command.command_id === throw_on) {
      const error = new Error('fixture listener failure');
      error.name = 'RuntimeError';
      throw error;
    }
  };
}
function wake_count(): number {
  return (mailbox as unknown as { poll_waits: Set<unknown> }).poll_waits.size;
}
async function timed_poll(args: unknown[]): Promise<unknown> {
  await new Promise<void>((resolve) => setImmediate(resolve));
  let active = true,
    previous = performance.now(),
    blocked_ms = 0;
  let heartbeat: ReturnType<typeof setImmediate>;
  const beat = () => {
    const current = performance.now();
    blocked_ms = Math.max(blocked_ms, current - previous);
    previous = current;
    if (active) heartbeat = setImmediate(beat);
  };
  heartbeat = setImmediate(beat);
  const start = performance.now(),
    writer_start = writer_ms;
  try {
    const reply = await mailbox.poll(
      args[0] as WireTypes['ExecutorIdentity'],
      args[1] as WireInteger,
      scalar(args[2]),
    );
    const polled_at = performance.now();
    const wire_bytes = encode_bounded(reply, 'MailboxPollReply', {
      limit: 4 * 1024 * 1024,
    }).byteLength;
    const encoded_at = performance.now();
    await new Promise<void>((resolve) => setImmediate(resolve));
    return {
      reply,
      timing: {
        poll_ms: polled_at - start,
        encode_ms: encoded_at - polled_at,
        full_ms: encoded_at - start,
        blocked_ms,
        writer_ms: writer_ms - writer_start,
        wire_bytes,
        commands: reply.commands.length,
      },
    };
  } finally {
    active = false;
    clearImmediate(heartbeat);
  }
}
async function poll_boundaries(args: unknown[]): Promise<unknown> {
  const identity = args[0] as WireTypes['ExecutorIdentity'],
    high = args[1] as WireInteger;
  const controller = new AbortController();
  for (let index = 0; index < 5; index++) {
    await mailbox.poll(identity, high, 0.002, { signal: controller.signal });
    if (wake_count() || getEventListeners(controller.signal, 'abort').length)
      throw new Error('An idle poll retained a wake or abort subscription');
  }
  const cancelled = [];
  for (const custom of [false, true]) {
    const abort = new AbortController(),
      reason = new Error('fixture abort reason');
    const poll = mailbox.poll(identity, high, 30, { signal: abort.signal });
    if (custom) abort.abort(reason);
    else abort.abort();
    try {
      await poll;
      throw new Error('Aborted poll returned');
    } catch (error) {
      if (!is_shutdown_business_cancellation(error)) throw error;
      cancelled.push({
        cancelled: true,
        wrapped: error !== reason,
        reason_preserved: !custom || (error as Error & { cause?: unknown }).cause === reason,
      });
    }
    if (wake_count() || getEventListeners(abort.signal, 'abort').length)
      throw new Error('An aborted poll retained a wake or abort subscription');
  }
  const overflow = await outcome(() => mailbox.poll(identity, high, 2 ** 31 / 1000));
  return {
    cancelled,
    idle_wakes: wake_count(),
    abort_subscribers: getEventListeners(controller.signal, 'abort').length,
    overflow,
  };
}
async function outcome(work: () => unknown | Promise<unknown>): Promise<Outcome> {
  try {
    return { ok: true, value: (await work()) ?? null };
  } catch (error) {
    return failure(error);
  }
}
function failure(error: unknown): Outcome {
  if (error instanceof DOMException && error.name === 'AbortError')
    return { ok: false, type: 'CancelledError', message: '' };
  if (!(error instanceof Error)) throw error;
  return {
    ok: false,
    type: error.name,
    message: error.message,
    ...(error instanceof AggregateError ? { errors: error.errors.map(failure) } : {}),
  };
}
async function call(method: string, args: unknown[]): Promise<unknown> {
  const command = args[0] as WireTypes['ChildCommand'];
  const identity = args[0] as WireTypes['ExecutorIdentity'];
  const options = observer ? { signal: observer.signal } : {};
  const calls: Record<string, () => unknown> = {
    put: () => mailbox.put_child_command(command, options),
    model: () => mailbox.put_model_list_request(args[0] as WireTypes['ModelListRequest']),
    poll: () => mailbox.poll(identity, args[1] as WireInteger, scalar(args[2]), options),
    wait: () =>
      mailbox.wait_result(args[0] as string, args[1] === null ? null : scalar(args[1]), options),
    report: () => mailbox.record_results(identity, args[1] as WireTypes['CommandOutcomeReport']),
    withdraw: () => mailbox.withdraw_waiting(args[0] as string, args[1] as 'delivery_withdrawn'),
    proof: () => mailbox.finish_by_proof(args[0] as string, args[1] as WireTypes['CommandOutcome']),
    high: () => mailbox.high_sequence(),
  };
  return calls[method]!();
}
async function execute(action: Action): Promise<unknown> {
  switch (action.op) {
    case 'open':
      // The previous fixture owner already completed its explicit stop controls.
      database?.close();
      signals = 0;
      trace = [];
      throw_on = null;
      held.clear();
      now = scalar(action.now);
      database = createDatabase(action.path, { onChange: () => signals++ });
      owner();
      return null;
    case 'clock':
      now = scalar(action.now);
      return null;
    case 'sql':
      sql(action.statements);
      return null;
    case 'arm':
      before_sql = action.before_sql;
      after_abort = action.after_abort ?? false;
      after_put = action.after_put;
      return null;
    case 'reentrant':
      return reentered;
    case 'listen':
      throw_on = action.throw_on;
      trace = [];
      return null;
    case 'call': {
      observer = after_abort ? new AbortController() : undefined;
      try {
        return await call(action.method, action.args);
      } finally {
        observer = undefined;
      }
    }
    case 'begin': {
      const task: Held = { promise: Promise.resolve({ ok: true, value: null }), done: false };
      task.promise = outcome(() => call(action.method, action.args)).then((result) => {
        task.done = true;
        return result;
      });
      held.set(action.key, task);
      await Promise.resolve();
      return {
        pending: !task.done,
        waiter: action.method === 'wait' ? mailbox.has_waiter(action.args[0] as string) : null,
      };
    }
    case 'join':
      return held.get(action.key)!.promise;
    case 'pending':
      return Object.fromEntries([...held].map(([key, task]) => [key, !task.done]));
    case 'snapshot':
      return { trace, signals };
    case 'timed_poll':
      return timed_poll(action.args);
    case 'poll_boundaries':
      return poll_boundaries(action.args);
    case 'wake_count':
      return wake_count();
    case 'inspect':
      return {
        open: mailbox.open_command_ids_of(action.child),
        withdrawn: mailbox.prompt_withdrawn_at_replacement(action.child, action.turn),
        rewritten: mailbox.prompts_written_again(),
        late: mailbox.late_steers_left_by_a_restart(),
        start: mailbox.start_of(action.child),
        input: mailbox.command_for_input(action.key),
        row: mailbox.command_row(action.key),
        stalls: database.read((sql) => mailbox.stall_source().tracked_waits(sql, now)),
        high: mailbox.high_sequence(),
      };
    case 'replace': {
      const settled = database.write((tx) => {
        for (const statement of action.statements)
          tx.run(statement.sql, statement.values.map(parameter));
        return mailbox.replace_incarnation_in_transaction(tx, {
          replaced: action.args[0] as string,
          current: action.args[1] as string,
          now_ms: now,
        });
      });
      mailbox.publish_settlements(settled);
      return null;
    }
    case 'reopen':
      await mailbox.stop({ deadline: performance.now() / 1000 + 5 });
      await mailbox.settled();
      owner();
      return null;
    case 'stop': {
      const before = [reads, writes];
      await mailbox.stop({
        deadline: performance.now() / 1000 + scalar(action.deadline_ms ?? 5000) / 1000,
      });
      await mailbox.settled();
      return { sql_after_seal: reads + writes - before[0]! - before[1]! };
    }
    case 'poststop':
      return database.write((tx) => ({
        before: stalls.tracked_waits(tx, now),
        settled: mailbox.replace_incarnation_in_transaction(tx, {
          replaced: action.args[0] as string,
          current: action.args[1] as string,
          now_ms: now,
        }),
        after: stalls.tracked_waits(tx, now),
      }));
    case 'sealed': {
      const before = reads + writes;
      const refused = await Promise.all([
        outcome(() => call('poll', action.args)),
        outcome(() => call('put', [action.command])),
        outcome(() => mailbox.high_sequence()),
        outcome(() => mailbox.wait_result('new', 0)),
      ]);
      return { refused, sql: reads + writes - before };
    }
    case 'pending_failure': {
      const report = outcome(() => call('report', action.args));
      const stop = await outcome(() => mailbox.stop({ deadline: performance.now() / 1000 }));
      const settled = await outcome(() => mailbox.settled());
      return { report: await report, stop, settled, waiting: mailbox.has_waiter(action.key) };
    }
    case 'cross_read': {
      const reader = createReadAccess(action.path);
      try {
        return reader.read((sql) =>
          action.ids.map((id) => ({
            row: data.command_row(sql, id),
            input: data.command_for_input(sql, id),
          })),
        );
      } finally {
        reader.close();
      }
    }
    case 'finish':
      database.close();
      return null;
    default:
      throw new Error('Unknown mailbox control: ' + action.op);
  }
}
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  const action = parse_executor_json(Buffer.from(line)) as unknown as Action;
  const result = await outcome(() => execute(action));
  process.stdout.write(stringify_python_json(result, { compact: true }) + '\n');
  if (action.op === 'finish') break;
}
