/// <reference lib="es2024.promise" />
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setImmediate as yield_turn } from 'node:timers/promises';
import { OutboxStore } from '../../storage/outbox.ts';
import { open_command_journal } from '../../storage/journal.ts';
import type { AsyncCommandJournal, CanonicalReader } from '../../storage/journal_contracts.ts';
import type { StopOptions } from '../../../../lifecycle/contracts.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../../lifecycle/contracts.ts';
import type {
  MailboxCommand,
  CommandResult,
  CommandOutcomeReply,
} from '../../../wire.generated.ts';
import type { MailboxRunner, MailboxCatalog, MailboxClient } from '../contracts.ts';
import { MacMailbox, MacCommandQueues, MacPollLoop, MacResultReporter } from '../index.ts';
import * as protocol from '../../../protocol.ts';
import { read_canonical_body } from '../poll.ts';
import { stringify_pydantic_json } from '../../../../core/http/numeric_json.ts';
import { OUTBOX_RETRY_INITIAL_SECONDS } from '../../configuration.ts';
interface Input {
  root: string;
  fixture: string;
  seed: string;
  poll: string;
  shutdown: string;
  committed: string;
  recovery_results: Record<string, [number, string]>;
  commands: Record<string, string>;
  results: Record<string, string>;
  report_commands: string;
  report_replies: string[];
  now_ms: string;
  performance: { poll: string };
}
const deferred = () => Promise.withResolvers<void>();
function aborted(signal: AbortSignal): Promise<never> {
  signal.throwIfAborted();
  return new Promise((_, reject) =>
    signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
  );
}
function canonical_reader(value: Buffer): CanonicalReader {
  let closed = false;
  return {
    byte_count: value.length,
    async *[Symbol.asyncIterator]() {
      for (let offset = 0; offset < value.length; offset += 256 * 1024) {
        if (closed) throw new Error('Reader already closed');
        yield value.subarray(offset, offset + 256 * 1024);
      }
    },
    async close() {
      closed = true;
    },
  };
}
const bytes = async (reader: CanonicalReader) => Buffer.from(await read_canonical_body(reader));
const request_bytes = (reader: CanonicalReader) =>
  bytes({
    byte_count: reader.byte_count,
    [Symbol.asyncIterator]: () => reader[Symbol.asyncIterator](),
    async close() {}, // The reporter retains its actual reader through feedback and close.
  });
const digest = (raw: Buffer) => [raw.length, createHash('sha256').update(raw).digest('hex')];
async function drain(cursor: Awaited<ReturnType<AsyncCommandJournal['record_poll_reply']>>) {
  let last: Buffer | undefined;
  try {
    for await (const row of cursor) last = await bytes(row.command);
  } finally {
    await cursor.close();
  }
  return last;
}
export async function mailbox_parity(input: Input): Promise<unknown> {
  const options = { deadline: performance.now() / 1000 + 25 };
  const spool = join(input.root, 'spool');
  const clock = { monotonic: () => 1, now_ns: () => BigInt(input.now_ms) * 1_000_000n };
  let store: OutboxStore | undefined, fault: Error | undefined;
  let queues: MacCommandQueues | undefined,
    poll: MacPollLoop | undefined,
    reporter: MacResultReporter | undefined;
  let aggregate: MacMailbox | undefined;
  const on_failure = (error: Error) => {
    fault ??= error;
    if (store) void cleanup().catch(() => {});
  };
  async function stop(
    service: { stop(options: StopOptions): Promise<void>; settled(): Promise<void> } | undefined,
  ) {
    if (!service) return;
    try {
      await service.stop(options);
    } finally {
      await service.settled();
    }
  }
  let cleanup_task: Promise<void> | undefined;
  function cleanup(): Promise<void> {
    return (cleanup_task ??= Promise.resolve().then(async () => {
      const first = await Promise.allSettled([stop(poll), stop(reporter)]);
      const second = await Promise.allSettled([stop(queues), stop(aggregate)]);
      const last = await Promise.allSettled([stop(store)]);
      const errors = [...first, ...second, ...last].flatMap((result) =>
        result.status === 'rejected' ? [result.reason] : [],
      );
      if (errors.length)
        throw new AggregateError(errors, 'Mailbox cleanup failed after settlement.');
    }));
  }
  store = await OutboxStore.open(spool, clock, options, on_failure);
  if (fault) on_failure(fault);
  const release_on_exit: (() => void)[] = [];
  const started = performance.now(),
    phases: Record<string, number[]> = {};
  const retry_times: Record<string, number[]> = { poll: [], runner: [] };
  let phase = 'journal-open';
  let next = started + 10,
    rss = process.memoryUsage().rss;
  const timer = setInterval(() => {
    const now = performance.now();
    (phases[phase] ??= []).push(Math.max(0, now - next));
    next = now + 10;
    rss = Math.max(rss, process.memoryUsage().rss);
  }, 10);
  let observations: unknown, performance_proof: unknown;
  let aggregate_shutdown: Record<string, boolean> = {};
  const debug = console.debug,
    cancellation_debug: unknown[][] = [];
  console.debug = (...args: unknown[]) => {
    if (args[0] === 'Mac command canceled during stop command=a1:') cancellation_debug.push(args);
    else debug(...args);
  };
  try {
    if (fault) throw fault;
    const dependency = new URL('./fixture.ts', import.meta.url),
      fault_file = join(input.root, 'feedback-fault');
    dependency.searchParams.set('fixture', input.fixture);
    dependency.searchParams.set('feedback_fault', fault_file);
    dependency.searchParams.set('database', join(input.root, 'spool/commands.sqlite3'));
    const journal_dependencies = {
      outputs_module: dependency,
      now_ms: () => BigInt(input.now_ms),
      on_failure,
    };
    let journal = await open_command_journal(store, 'inc', journal_dependencies, options);
    phase = 'queues';
    const command = (id: string): MailboxCommand =>
      protocol.decode_json(Buffer.from(input.commands[id]!, 'hex'), 'MailboxCommand');
    const result = (id: string): CommandResult =>
      protocol.decode_json(Buffer.from(input.results[id]!, 'hex'), 'CommandResult');
    const finished = new Map<string, ReturnType<typeof deferred>>();
    for (const id of ['a1', 'a2', 'b', 'stop', 'model', 'shutdown']) finished.set(id, deferred());
    const planned = deferred(),
      planned_waiting = deferred(),
      first = deferred(),
      release = deferred(),
      stopped = deferred(),
      replayed = deferred();
    const catalog_entered = deferred(),
      catalog_release = deferred(),
      cancelled_run = deferred();
    let cancel_next_run = false,
      cancelled_runs = 0;
    release_on_exit.push(planned.resolve, release.resolve, catalog_release.resolve);
    const calls: string[] = [],
      completed: string[] = [],
      replays: string[] = [],
      catalog_reads: string[] = [];
    let owner_closes = 0;
    const result_finished = () => {
      if (owner_closes) finished.get('model')!.resolve();
    };
    const runner: MailboxRunner = {
      admission_stopped: false,
      async planned_replay_finished(signal) {
        planned_waiting.resolve();
        await Promise.race([planned.promise, aborted(signal)]);
      },
      async run(child, signal) {
        signal.throwIfAborted();
        const id = child.command_id;
        calls.push(id);
        if (cancel_next_run) {
          cancelled_run.resolve();
          try {
            await aborted(signal);
          } catch {
            cancelled_runs++;
            throw new Error('scripted ordinary runner abort');
          }
        }
        if (id === 'b') {
          retry_times.runner!.push(performance.now() / 1000);
          if (retry_times.runner!.length === 1)
            throw Object.assign(new Error('scripted runner retry'), { name: 'OSError' });
        }
        if (id === 'a1' || id === 'stop') {
          (id === 'a1' ? first : stopped).resolve();
          await Promise.race([release.promise, aborted(signal)]);
        }
        await journal.finish(id, Buffer.from(input.results[id]!, 'hex'), id === 'stop');
        if (id === 'stop') await journal.mark_outputs_published(id);
        completed.push(id);
        finished.get(id)!.resolve();
        return result(id).child_outcome!;
      },
      async publish_owed_outputs(c, r) {
        const decoded = protocol.decode_json(await bytes(c), 'MailboxCommand');
        await bytes(r);
        replays.push(decoded.command_id);
        await journal.mark_outputs_published(decoded.command_id);
        replayed.resolve();
      },
    };
    const catalog: MailboxCatalog = {
      async read_result(id) {
        catalog_reads.push(id);
        if (id === 'shutdown') {
          catalog_entered.resolve();
          await catalog_release.promise;
        }
        return {
          async open() {
            return canonical_reader(Buffer.from(input.results[id]!, 'hex'));
          },
          async close() {
            owner_closes++;
          },
        };
      },
    };
    let poll_calls = 0,
      ordinary_refused = false;
    const reports: unknown[] = [];
    const capacity_tasks: Promise<bigint>[] = [];
    const client: MailboxClient = {
      async poll_commands(_after, _hold, signal) {
        if (++poll_calls === 1) {
          retry_times.poll!.push(performance.now() / 1000);
          throw Object.assign(new Error('scripted poll retry'), { name: 'ConnectionError' });
        }
        if (poll_calls === 2) {
          retry_times.poll!.push(performance.now() / 1000);
          return Buffer.from(input.poll, 'hex');
        }
        return aborted(signal);
      },
      async report_outcomes(body) {
        const raw = await request_bytes(body);
        reports.push(digest(raw));
        if (reports.length === 1) {
          writeFileSync(fault_file, 'fail once');
          for (let i = 0; i < 32; i++) capacity_tasks.push(journal.poll_cursor());
          capacity_tasks.push(
            journal.poll_cursor().catch((error: unknown) => {
              const refusal = error as { code?: unknown; unadmitted?: unknown };
              if (refusal.code !== 'EBUSY' || !refusal.unadmitted) throw error;
              ordinary_refused = true;
              return 0n;
            }),
          );
        }
        if (reports.length === 5) void reporter!.stop(options).catch(on_failure);
        return protocol.decode_json(
          Buffer.from(input.report_replies[reports.length - 1]!, 'hex'),
          'CommandOutcomeReply',
        ) as CommandOutcomeReply;
      },
    };
    await drain(await journal.record_poll_reply(Buffer.from(input.seed, 'hex'), 'inc', 0n));
    await journal.finish('duplicate', Buffer.from(input.results.duplicate!, 'hex'), true);
    await journal.mark_outputs_published('duplicate');
    queues = new MacCommandQueues({
      runner,
      catalog,
      journal,
      result_finished,
      on_failure,
    });
    poll = new MacPollLoop(
      {
        runner,
        client,
        journal,
        incarnation: 'inc',
        result_finished,
        on_failure,
      },
      queues,
    );
    poll.start();
    await planned_waiting.promise;
    const planned_blocked = poll_calls === 0;
    planned.resolve();
    await Promise.all([
      first.promise,
      stopped.promise,
      finished.get('b')!.promise,
      finished.get('model')!.promise,
      replayed.promise,
    ]);
    const second_not_started = !calls.includes('a2');
    release.resolve();
    await Promise.all(['a1', 'a2', 'stop'].map((id) => finished.get(id)!.promise));
    await stop(poll);
    poll = undefined;
    const fifo =
      calls.indexOf('a1') < calls.indexOf('a2') &&
      completed.indexOf('a1') < completed.indexOf('a2');
    // The reporter phase reuses the worker and discards no durable rows.
    phase = 'reporter';
    await journal.mark_reported(['duplicate', 'a1', 'a2', 'b', 'stop', 'model']);
    await drain(
      await journal.record_poll_reply(Buffer.from(input.report_commands, 'hex'), 'inc', 0n),
    );
    for (const id of ['f', 'k', 'r'])
      await journal.finish(id, Buffer.from(input.results[id]!, 'hex'), false);
    const acknowledged = deferred();
    const capacity_errors: unknown[] = [];
    const observed = new Proxy(journal, {
      get(target, key) {
        if (key === 'report_feedback')
          return async (...args: Parameters<AsyncCommandJournal['report_feedback']>) => {
            try {
              const progressed = await target.report_feedback(...args);
              if (Array.isArray(args[1]) && args[1].includes('f') && progressed)
                acknowledged.resolve();
              return progressed;
            } catch (error) {
              if ((error as { unadmitted?: boolean }).unadmitted) capacity_errors.push(error);
              throw error;
            }
          };
        const value = Reflect.get(target, key, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    reporter = new MacResultReporter({ client, journal: observed, on_failure });
    reporter.start();
    await acknowledged.promise;
    await Promise.all(capacity_tasks);
    await stop(reporter);
    await journal.shrink_to_tombstones();
    reporter = undefined;
    // Admission covers a catalog read and its later result write through stop.
    phase = 'catalog-shutdown';
    await drain(await journal.record_poll_reply(Buffer.from(input.shutdown, 'hex'), 'inc', 0n));
    queues.submit(command('shutdown'));
    await catalog_entered.promise;
    let actual_settled = false;
    const actual = queues.settled().then(() => {
      actual_settled = true;
    });
    const stopping = queues.stop(options);
    await Promise.resolve();
    const stop_waited = !actual_settled;
    catalog_release.resolve();
    await stopping;
    await actual;
    const stored = await journal.result_of('shutdown');
    if (!stored) throw new Error('Admitted model result was lost');
    if (owner_closes !== 2) throw new Error('Catalog canonical owners did not close exactly once');
    if (fault) throw fault;
    observations = {
      planned_blocked,
      second_not_started,
      fifo,
      late_reply_ack_applied: true,
      calls: [...calls],
      retry_delays: Object.fromEntries(
        Object.entries(retry_times).map(([key, times]) => [
          key,
          times[1]! - times[0]! >= OUTBOX_RETRY_INITIAL_SECONDS - 0.01,
        ]),
      ),
      replays,
      catalog_reads,
      reports,
      stop_waited,
      shutdown_result: digest(await bytes(stored)),
    };
    // Reuse the same worker for the TS aggregate's own stop/preparation boundary.
    phase = 'aggregate-shutdown';
    const prepared = deferred(),
      release_selection = deferred(),
      poll_waiting = deferred(),
      release_delivery = deferred();
    release_on_exit.push(release_selection.resolve, release_delivery.resolve);
    let selections = 0,
      cancelled_feedback = false;
    const cleanup_capacity_tasks: Promise<bigint>[] = [];
    const saturate_cleanup = () => {
      for (let i = 0; i < 32; i++) cleanup_capacity_tasks.push(journal.poll_cursor());
    };
    const prepared_journal = new Proxy(journal, {
      get(target, key) {
        if (key === 'report_feedback')
          return async (...args: Parameters<AsyncCommandJournal['report_feedback']>) => {
            const progressed = await target.report_feedback(...args);
            if (args[1] === 'cancelled') {
              cancelled_feedback = true;
              saturate_cleanup();
            }
            return progressed;
          };
        if (key === 'record_decoded_poll_reply')
          return async (...args: Parameters<AsyncCommandJournal['record_decoded_poll_reply']>) => {
            const fresh = await target.record_decoded_poll_reply(...args);
            poll_waiting.resolve();
            await release_delivery.promise;
            return fresh;
          };
        if (key === 'select_report')
          return async () => {
            const selected = await target.select_report();
            if (selected === null)
              throw new Error('Aggregate phase requires the admitted model result');
            selections++;
            return selected;
          };
        const value = Reflect.get(target, key, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    let late_http = 0,
      aggregate_reports = 0;
    aggregate = new MacMailbox({
      journal: prepared_journal,
      catalog,
      incarnation: 'inc',
      on_failure,
      runner: {
        ...runner,
        async planned_replay_finished(signal) {
          signal.throwIfAborted();
        },
      },
      client: {
        async poll_commands(_after, _hold, signal) {
          if (signal.aborted) late_http++;
          return Buffer.from(input.committed, 'hex');
        },
        async report_outcomes(body, signal) {
          if (signal.aborted) late_http++;
          await request_bytes(body);
          if (++aggregate_reports === 1)
            return { recorded: 1, rejected: [] } as unknown as CommandOutcomeReply;
          prepared.resolve();
          await release_selection.promise;
          saturate_cleanup();
          throw new Error('scripted request failure after stop');
        },
      },
    });
    const starting = aggregate.start();
    await Promise.all([prepared.promise, starting, poll_waiting.promise]);
    const calls_before_cancel = calls.length;
    cancel_next_run = true;
    aggregate.queues.submit(command('a1'));
    await cancelled_run.promise;
    let aggregate_joined = false;
    const joined = aggregate.settled().then(() => {
      aggregate_joined = true;
    });
    void joined.catch(() => {});
    const expired = await aggregate.stop({ deadline: performance.now() / 1000 - 1 }).then(
      () => null,
      (error) => error,
    );
    const actual_waited = !aggregate_joined;
    let sealed = false;
    try {
      aggregate.queues.submit(command('a1'));
    } catch (error) {
      if (!(error instanceof ServerLifecycleError)) throw error;
      sealed = true;
    }
    release_delivery.resolve();
    await aggregate.poll.settled();
    release_selection.resolve();
    await joined;
    await Promise.all(cleanup_capacity_tasks);
    const released = await journal.select_report();
    if (!released || released.command_ids.length !== 1 || released.command_ids[0] !== 'shutdown')
      throw new Error('Aggregate failed to release its worker report selection');
    try {
      await journal.report_feedback(released.selection, 'cancelled');
    } finally {
      await released.body.close();
    }
    const restarted = await aggregate.start().then(
      () => null,
      (error) => error,
    );
    aggregate_shutdown = {
      owned_feedback_not_refused: capacity_errors.length === 0 && ordinary_refused,
      typed_expired_observer:
        expired instanceof ShutdownDeadlineExceeded && expired instanceof ServerLifecycleError,
      actual_waited,
      queue_sealed_before_preparation_return: sealed,
      no_late_http: late_http === 0,
      malformed_reply_released_selection: selections === 2 && aggregate_reports === 2,
      committed_not_dispatched: !calls.includes('committed') && !calls.includes('committed-later'),
      ordinary_runner_abort_once:
        cancelled_runs === 1 &&
        calls.length === calls_before_cancel + 1 &&
        cancellation_debug.length === 1 &&
        cancellation_debug[0]![1] === 'Error',
      owned_cleanup_under_saturation: cancelled_feedback && cleanup_capacity_tasks.length === 64,
      selection_released: true,
      restart_refused: restarted instanceof ServerLifecycleError,
    };
    const recovery_started = performance.now();
    phase = 'healthy-cleanup-and-recovery';
    journal.assert_healthy();
    let journal_joined = false;
    const journal_actual = journal.settled().finally(() => {
      journal_joined = true;
    });
    void journal_actual.catch(() => {});
    await yield_turn();
    const retained = !journal_joined;
    await stop(journal);
    await journal_actual;
    await stop(store);
    store = undefined;
    const owned_cleanup_ms = performance.now() - recovery_started;
    Object.assign(aggregate_shutdown, {
      clean_stop_without_failure: !fault,
      worker_retained_until_join: retained && journal_joined,
    });
    store = await OutboxStore.open(spool, clock, options, on_failure);
    journal = await open_command_journal(store, 'inc', journal_dependencies, options);
    for (const [id, expected] of Object.entries(input.recovery_results)) {
      const recovered = await journal.result_of(id);
      if (!recovered || JSON.stringify(digest(await bytes(recovered))) !== JSON.stringify(expected))
        throw new Error('Undispatched committed command did not recover uncertain.');
      await journal.mark_outputs_published(id);
    }
    await journal.mark_reported(['shutdown', ...Object.keys(input.recovery_results)]);
    const recovery_ms = performance.now() - recovery_started;
    phase = 'performance-setup';
    const poll_bytes = Buffer.from(input.performance.poll);
    phase = 'decoded-poll-delivery';
    const started_poll = performance.now();
    const decoded = await journal.record_decoded_poll_reply(poll_bytes, 'inc', 0n);
    let expanded: MailboxCommand | undefined;
    try {
      for await (const value of decoded) expanded = value;
    } finally {
      await decoded.close();
    }
    if (!expanded) throw new Error('Expansion command was lost');
    const poll_ms = performance.now() - started_poll;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    phase = 'canonical-evidence-only';
    const encoding_started = performance.now();
    const canonical = Buffer.from(
      stringify_pydantic_json(protocol.encode_value(expanded, 'MailboxCommand')),
    );
    const encoding_ms = performance.now() - encoding_started;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    const performance_reports = [],
      selection_ms = [];
    for (const [index, size] of [4 * 1024 * 1024 - 4096, 4 * 1024 * 1024 + 4096].entries()) {
      phase = index === 0 ? 'near-limit-report' : 'oversized-report';
      const id = `size-${index + 16}`;
      const value = { ...command('model'), command_sequence: index + 16, command_id: id };
      await drain(
        await journal.record_poll_reply(JSON.stringify({ commands: [value] }), 'inc', 0n),
      );
      await journal.finish(
        id,
        JSON.stringify({ command_id: id, model_list: { text: 'x'.repeat(size) } }),
        false,
      );
      const started_selection = performance.now(),
        selected = await journal.select_report();
      selection_ms.push(performance.now() - started_selection);
      if (!selected || selected.command_ids.join() !== id)
        throw new Error('Report selection lost singleton');
      performance_reports.push(digest(await bytes(selected.body)));
      await journal.report_feedback(selected.selection, [id]);
    }
    performance_proof = {
      raw_poll_bytes: poll_bytes.length,
      canonical_command: digest(canonical),
      decoded_delivery_ms: poll_ms,
      canonical_evidence_ms: encoding_ms,
      recovery_ms,
      owned_cleanup_ms,
      selection_ms,
      reports: performance_reports,
    };
  } finally {
    for (const release of release_on_exit) release();
    try {
      await cleanup();
    } finally {
      console.debug = debug;
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      clearInterval(timer);
      rss = Math.max(rss, process.memoryUsage().rss);
    }
    if (fault) throw fault;
  }
  const delays = Object.values(phases)
    .flat()
    .sort((a, b) => a - b);
  return {
    observations,
    aggregate_shutdown,
    performance: performance_proof,
    stats: {
      elapsed_seconds: (performance.now() - started) / 1000,
      samples: delays.length,
      max_lateness_ms: delays.at(-1) ?? 0,
      p99_lateness_ms: delays[Math.ceil(delays.length * 0.99) - 1] ?? 0,
      max_rss_bytes: rss,
      retry_seconds: Object.fromEntries(
        Object.entries(retry_times).map(([key, times]) => [key, times[1]! - times[0]!]),
      ),
      phases: Object.fromEntries(
        Object.entries(phases).map(([name, samples]) => [
          name,
          {
            samples: samples.length,
            max_lateness_ms: Math.max(...samples),
          },
        ]),
      ),
    },
  };
}
