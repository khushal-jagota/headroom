/** Fixed controls over one real mailbox and application database, without a host. */
import { createInterface } from 'node:readline';
import { getEventListeners } from 'node:events';
import type { Database, SqlValue } from '../../core/database/contracts.ts';
import { createDatabase, createReadAccess } from '../../core/database/index.ts';
import { SqliteCommandMailbox } from '../vps_mailbox.ts';
import { encode_value, encode_bounded } from '../protocol.ts';
import { is_shutdown_business_cancellation } from '../../conversation/shutdown.ts';
import type { StallSource } from '../../stalls/contracts.ts';
import * as data from '../data/mailbox.ts';
import type { WireTypes } from '../wire.generated.ts';
import { MacPresenceService, no_update_check } from '../vps_presence.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { UpdateConflict } from '../../environments/updates/attempts.ts';
import { ExecutorValidationError, decode_value } from '../protocol.ts';
import { read_presence } from '../vps_presence_store.ts';

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
  turn: number;
  deadline_ms?: number;
  command: WireTypes['ChildCommand'];
  ids: string[];
  mode?: string;
  nested?: WireTypes['LeaseRequest'];
};
type Outcome =
  | { ok: true; value: unknown }
  | {
      ok: false;
      type: string;
      message: string;
      errors?: Outcome[];
      issues?: { type: string; loc: readonly (string | number)[] }[];
    };
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
let presence: MacPresenceService | undefined,
  presence_mode = '';
let callback_release: (() => void) | undefined;
let callback_gate: Promise<void> | undefined;
let callback_trace: unknown[] = [];
let change_reads: unknown[] = [];
let presence_after_commit: (() => void) | undefined;
const changes = new ChangeSignal();
changes.subscribe(() => {
  signals++;
  if (presence)
    change_reads.push({
      health: presence.health(),
      present: presence.is_present(),
      record: presence.record(),
      sql: database.read(read_presence),
    });
});
const scalar = (value: unknown): number => {
  if (typeof value !== 'number') throw new TypeError('A control number is required');
  return value;
};
const parameter = (value: SqlValue): SqlValue => value;
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
    if (presence_mode === 'fail_contact') {
      callback_trace.push(['contact_failure']);
      throw Object.assign(new Error('fixture contact flush'), { code: 'EIO' });
    }
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
    const after = presence_after_commit;
    presence_after_commit = undefined;
    after?.();
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
      throw new Error('fixture listener failure');
    }
  };
}
function wake_count(): number {
  return (mailbox as unknown as { poll_waits: Set<unknown> }).poll_waits.size;
}
async function timed_poll(args: unknown[], lease = false): Promise<unknown> {
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
    const reply = lease
      ? await presence!.accept_lease(args[0] as WireTypes['LeaseRequest'])
      : await mailbox.poll(
          args[0] as WireTypes['ExecutorIdentity'],
          args[1] as number,
          scalar(args[2]),
        );
    const polled_at = performance.now();
    const wire_bytes = lease
      ? encode_bounded(reply as WireTypes['LeaseReply'], 'LeaseReply').byteLength
      : encode_bounded(reply as WireTypes['MailboxPollReply'], 'MailboxPollReply', {
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
        commands: lease ? 0 : (reply as WireTypes['MailboxPollReply']).commands.length,
      },
    };
  } finally {
    active = false;
    clearImmediate(heartbeat);
  }
}
async function poll_boundaries(args: unknown[]): Promise<unknown> {
  const identity = args[0] as WireTypes['ExecutorIdentity'],
    high = args[1] as number;
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
    poll: () => mailbox.poll(identity, args[1] as number, scalar(args[2]), options),
    wait: () =>
      mailbox.wait_result(args[0] as string, args[1] === null ? null : scalar(args[1]), options),
    report: () => mailbox.record_results(identity, args[1] as WireTypes['CommandOutcomeReport']),
    withdraw: () => mailbox.withdraw_waiting(args[0] as string, args[1] as 'delivery_withdrawn'),
    proof: () => mailbox.finish_by_proof(args[0] as string, args[1] as WireTypes['CommandOutcome']),
    high: () => mailbox.high_sequence(),
  };
  return calls[method]!();
}
function callback_error(): void {
  const kind = presence_mode;
  if (kind === 'os') throw Object.assign(new Error('fixture callback'), { code: 'EIO' });
  if (kind === 'value') throw new ExecutorValidationError('fixture callback');
  if (kind === 'conflict') throw new UpdateConflict('fixture callback');
  if (kind === 'type') throw new TypeError('fixture callback');
  if (kind === 'syntax') throw new SyntaxError('fixture callback');
  if (kind === 'validation') decode_value({}, 'MacUpdateCheck');
  if (kind === 'sqlite') database.read((sql) => sql.get('SELECT * FROM missing_fixture_table'));
}
function presence_owner(): void {
  presence = new MacPresenceService(facade, mailbox, {
    changes,
    application_revision: 'fixture',
    clock: () => now,
  });
  presence.update_check = async () => {
    callback_trace.push(['update']);
    if (presence_mode !== 'sqlite') callback_error();
    return no_update_check();
  };
  presence.incarnation_replaced = async (old, current) => {
    callback_trace.push(['replace', old, current]);
    if (presence_mode === 'hold_replace') await callback_gate;
    callback_error();
  };
  presence.returned = () => {
    callback_trace.push(['returned']);
  };
}
async function presence_control(action: Action): Promise<unknown> {
  const p = presence!;
  switch (action.method) {
    case 'open':
      presence_owner();
      callback_trace = [];
      return null;
    case 'mode':
      presence_mode = action.mode!;
      callback_gate = new Promise<void>((resolve) => {
        callback_release = resolve;
      });
      return null;
    case 'release':
      callback_release?.();
      return null;
    case 'lease':
      return p.accept_lease(action.args[0] as WireTypes['LeaseRequest']);
    case 'contact':
      return p.record_contact();
    case 'refuse':
      return p.refuse_old_websocket();
    case 'fence':
      return p.require_current(action.args[0] as Parameters<typeof p.require_current>[0]);
    case 'activity':
      return p.note_conversation_activity(action.key);
    case 'drain':
      return p.wait_until_drained((action.args[0] as string) ?? action.key);
    case 'callbacks':
      return callback_trace;
    case 'changes':
      return change_reads;
    case 'timed_lease':
      return timed_poll(action.args, true);
    case 'cache_only':
      p.incarnation_replaced = null;
      p.returned = null;
      return null;
    case 'cursor_race': {
      const before = mailbox.high_sequence();
      const placed = database.write((tx) =>
        data.insert_child_command(tx, action.args[1] as WireTypes['ChildCommand'], now),
      );
      const reply = await p.accept_lease(action.args[0] as WireTypes['LeaseRequest']);
      return { placed, reply, cached: mailbox.high_sequence(), before };
    }
    case 'mailbox_stop':
      await mailbox.stop({ deadline: performance.now() / 1000 + 5 });
      await mailbox.settled();
      return null;
    case 'state':
      return {
        record: p.record(),
        health: p.health(),
        judgement: p.judgement(),
        identity: p.current_identity(),
        present: p.is_present(),
        waits: database.read((sql) => p.stall_source().tracked_waits(sql, now)),
        callbacks: callback_trace,
      };
    case 'cached': {
      const before = reads + writes;
      p.record();
      p.health();
      p.judgement();
      p.current_identity();
      p.is_present();
      return reads + writes - before;
    }
    case 'stored':
      return database.read(read_presence);
    case 'reopen':
      await p.stop({ deadline: performance.now() / 1000 + 5 });
      await p.settled();
      presence_owner();
      return null;
    case 'stop':
      await p.stop({
        deadline: performance.now() / 1000 + scalar(action.deadline_ms ?? 5000) / 1000,
      });
      await p.settled();
      return null;
    case 'arm':
      presence_after_commit = () => {
        if (action.mode === 'stop') void p.stop({ deadline: performance.now() / 1000 + 5 });
        if (action.mode === 'clock') now = scalar(action.now);
        if (action.nested) reentered = p.accept_lease(action.nested);
      };
      return null;
    default:
      throw new Error('Unknown presence control');
  }
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
    case 'presence':
      return presence_control(action);
    case 'presence_cold': {
      const private_db = createDatabase(action.path);
      let touched = 0;
      const cold = new MacPresenceService(
        {
          read(work) {
            touched++;
            return private_db.read(work);
          },
          write(work, options) {
            touched++;
            return private_db.write(work, options);
          },
          close() {
            throw new Error('Supplied database is not owned');
          },
        },
        mailbox,
        { changes },
      );
      try {
        await cold.stop({ deadline: performance.now() / 1000 + 5 });
        await cold.settled();
        const read = cold.health();
        cold.record_contact();
        cold.note_conversation_activity('cold');
        return { touched, read, record: cold.record(), present: cold.is_present() };
      } finally {
        private_db.close();
      }
    }
    case 'presence_begin': {
      const task: Held = { promise: Promise.resolve({ ok: true, value: null }), done: false };
      task.promise = outcome(() => presence_control(action)).then((result) => {
        task.done = true;
        return result;
      });
      held.set(action.key, task);
      await new Promise<void>((resolve) => setImmediate(resolve));
      return { pending: !task.done };
    }
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
  const action = JSON.parse(line) as unknown as Action;
  const result = await outcome(() => execute(action));
  process.stdout.write(JSON.stringify(result) + '\n');
  if (action.op === 'finish') break;
}
