/** Real command/source/retirement custody and stores with scripted skills, files and backend. */
import { readFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { setImmediate as yield_turn } from 'node:timers/promises';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import {
  BackendPromptAccepted,
  BackendPromptRejected,
  BackendSteerAccepted,
  BackendSteerRefused,
  BackendSteerUncertain,
  PromptWriteFailed,
} from '../../../conversation/backends/contracts.ts';
import type {
  BackendChild,
  TurnToken as NativeTurn,
} from '../../../conversation/backends/contracts.ts';
import { same_turn as claude_same_turn } from '../../../conversation/backends/claude_agent_sdk/turn.ts';
import { same_turn as codex_same_turn } from '../../../conversation/backends/codex_app_server/turn.ts';
import type { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import { decode_json, as_object, encode_value } from '../../protocol.ts';
import { MacCommandService } from '../runner/service.ts';
import { create_mac_output_publication } from '../runner/publication.ts';
import type { MacCommandDependencies, MacOutputPublication } from '../runner/command_contracts.ts';
import { MacNativeChildren } from '../runner/start_stop.ts';
import { MacChildState } from '../runner/state.ts';
import { MacSourceWork } from '../runner/source_work.ts';
import { MacRetirement } from '../runner/retirement.ts';
import { MacMaintenance } from '../runner/maintenance.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import { ConversationStartAborted } from '../../../runtime/abort.ts';
import { ExecutorPaths } from '../configuration.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { open_command_journal } from '../storage/journal.ts';
import { read_canonical_body } from '../mailbox/poll.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';

const started = performance.now(),
  root = process.argv[2]!;
const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as {
  commands: Record<string, string>;
  asks: string[];
};
// Wire numbers remain inside canonical strings until the authoritative codec reads them.
const commands = Object.fromEntries(
  Object.entries(input.commands).map(([key, raw]) => [key, decode_json(raw, 'ChildCommand')]),
);
const calls: unknown[] = [],
  trace: unknown[] = [],
  outcomes: unknown[] = [],
  states: unknown[] = [];
const facts: Record<string, boolean> = {};
const options = { deadline: performance.now() / 1000 + 25 };
const faults = new Set(['finish', 'append', 'mark', 'replay']);
const failures: Error[] = [];
const on_failure = (error: Error) => {
  failures.push(error);
};
const now = 1_700_000_010_123;
const update_id = '0123456789abcdef0123456789abcdef';
function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const entered = gate(),
  release = gate(),
  input_entered = gate(),
  zero_entered = gate(),
  finish_release = gate();
const source_release = gate(),
  replay_entered = gate(),
  replay_release = gate();
const maintenance_entered = gate(),
  maintenance_release = gate(),
  cleanup_release = gate();
let maintenance_blocked = false,
  cleanup_native_failure: Error | null = null;
let store: OutboxStore, journal: AsyncCommandJournal;
async function open() {
  store = await OutboxStore.open(
    join(root, 'spool'),
    {
      monotonic: () => 100,
      now_ns: () => BigInt(now) * 1_000_000n,
    },
    options,
    on_failure,
  );
  journal = await open_command_journal(
    store,
    'inc',
    {
      outputs_module: new URL('../runner/logic/outcomes.ts', import.meta.url),
      now_ms: () => BigInt(now),
      on_failure,
    },
    options,
  );
}
async function close() {
  // The journal owns a sidecar of this spool: join it before closing its parent owner.
  const results = await Promise.allSettled([journal.stop(options)]);
  try {
    await journal.settled();
  } finally {
    await store.stop(options);
    await store.settled();
  }
  if (results[0]?.status === 'rejected') throw results[0].reason;
}
const child_state = new MacChildState();
const native = {
  async start(
    resolved: ResolvedConversationStart,
    values: { vendor_session_cursor?: string | null },
  ) {
    calls.push([
      'start',
      resolved.model,
      { vendor_session_cursor: values.vendor_session_cursor ?? null },
    ]);
  },
  async write_prompt(turn: NativeTurn, content: { text?: string }[], values: unknown) {
    calls.push(['prompt', turn, content, values]);
    const text = content[0]?.text ?? '';
    if (text.endsWith('accepted')) {
      entered.resolve();
      await release.promise;
    }
    if (text.endsWith('maintenance')) {
      maintenance_blocked = true;
      maintenance_entered.resolve();
      await maintenance_release.promise;
    }
    if (text.endsWith('reject')) throw new BackendPromptRejected('not admitted 🦉'.repeat(6000));
    if (text.endsWith('replay')) throw new PromptWriteFailed('delivery fate unknown 🦉');
    return new BackendPromptAccepted({ composed_content_delivered: false });
  },
  async steer(turn: NativeTurn, content: { text?: string }[], values: unknown) {
    const active = native_children.get('child')!.active_turn as unknown as NativeTurn;
    facts.native_turn_identity =
      (facts.native_turn_identity ?? true) &&
      turn === active &&
      claude_same_turn(turn, active) &&
      codex_same_turn(turn, active);
    calls.push(['steer', turn, content, values]);
    const text = content[0]?.text ?? '';
    if (text.endsWith('refused'))
      return new BackendSteerRefused({ refusal_reason: 'no_running_turn_to_steer_into' });
    if (text.endsWith('uncertain'))
      return new BackendSteerUncertain({ composed_content_delivered: null });
    return new BackendSteerAccepted({ composed_content_delivered: false });
  },
  async cancel_running_turn() {
    calls.push(['cancel']);
  },
  async answer_permission_ask(ask_id: string, option_id: string) {
    calls.push(['permission', ask_id, option_id]);
  },
  async answer_user_input(request_id: string, answers: unknown) {
    calls.push(['question', request_id, answers]);
    const current = child_state.get(commands.question!.child, request_id)!;
    child_state.install({ ...current, turn: { ...current.turn, turn_number: 2 } });
  },
  async stop() {
    calls.push(['stop']);
    if (maintenance_blocked) {
      maintenance_blocked = false;
      maintenance_release.resolve();
    }
    if (cleanup_native_failure !== null) {
      const failure = cleanup_native_failure;
      cleanup_native_failure = null;
      throw failure;
    }
  },
} as unknown as BackendChild;
mkdirSync(join(root, 'workspace'), { recursive: true });
const native_children = new MacNativeChildren({
  paths: new ExecutorPaths(join(root, 'state')),
  workspace_home: root,
  factory: () => () => native,
  native_skill_home: () => join(root, 'native'),
  event_sink: () => ({
    async report() {
      throw new Error('Event custody belongs to PR5.');
    },
  }),
  message_files: null as unknown as ConversationMessageFiles,
  files: {
    async open_directory(path) {
      mkdirSync(path, { recursive: true });
      return { id: 0 };
    },
    async close_directory() {},
  },
  skills: {
    async install_native_skill_snapshot() {
      return join(root, 'skills');
    },
    async stop() {},
    async settled() {},
  },
  record_children: async () => {},
  on_failure,
});
const sources = new MacSourceWork(child_state, () => native_children.admission_stopped);
const retirement = new MacRetirement({ native_children, state: child_state, sources });
const state = () => {
  const child = native_children.get('child')!;
  states.push({
    turn: child.active_turn,
    model: child.model,
    effort: child.reasoning_effort,
    asks: child_state
      .entries(child.reference)
      .map((ask) => ask.ask_id)
      .sort(),
  });
};
const digest = (key: string) => createHash('sha256').update(key).digest('hex');
function wrap_journal(actual: AsyncCommandJournal): AsyncCommandJournal {
  return new Proxy(actual, {
    get(target, key) {
      if (key === 'finish')
        return async (id: string, result: Uint8Array | string, owed: boolean) => {
          trace.push(['finish', id, owed]);
          if (id === 'zero-held') {
            zero_entered.resolve();
            await finish_release.promise;
            if (faults.delete('finish')) throw new Error('scripted journal finish');
          }
          await target.finish(id, result, owed);
        };
      if (key === 'mark_outputs_published')
        return async (id: string) => {
          trace.push(['mark', id]);
          if (id === 'permission' && faults.delete('mark'))
            throw new Error('scripted publication mark');
          await target.mark_outputs_published(id);
        };
      const value = Reflect.get(target, key);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}
function wrap_publication(actual: MacOutputPublication): MacOutputPublication {
  return {
    async add(entry) {
      trace.push(['append', entry.submission_id]);
      if (entry.submission_id === 'turn_failed_' + digest('replay') && faults.delete('replay'))
        throw new Error('scripted append before commit');
      await actual.add(entry);
      if (entry.submission_id === 'turn_failed_' + digest('reject') && faults.delete('append'))
        throw new Error('scripted append after commit');
    },
  };
}
const dependencies = (
  owned_journal: AsyncCommandJournal,
  publication: MacOutputPublication,
): MacCommandDependencies => ({
  incarnation: 'inc',
  native_children,
  journal: owned_journal,
  publication,
  state: child_state,
  asks: child_state,
  inputs: {
    async download_inputs(command, signal) {
      calls.push(['inputs', command.command_id]);
      input_entered.resolve();
      await new Promise<void>((_resolve, reject) => {
        const aborted = () => reject(signal.reason);
        if (signal.aborted) aborted();
        else signal.addEventListener('abort', aborted, { once: true });
      });
    },
  },
  stop_exact: (command, work, invoking) => retirement.stop_exact(command, work, invoking),
  now_ms: () => now,
});
await open();
let service = new MacCommandService(
  dependencies(wrap_journal(journal!), wrap_publication(create_mac_output_publication(store!))),
);
const original_service = service;
const maintenance = new MacMaintenance({
  native_children,
  state: child_state,
  sources,
  commands: () => service,
  outbox: { pending_count: () => store!.pending_count() },
  new_update_id: () => update_id,
  on_failure,
});
async function run(key: string, fail = false) {
  try {
    const value = await service.run(commands[key]!);
    if (fail) throw new Error('Expected a durable-step failure.');
    outcomes.push([key, as_object(value, 'CommandOutcome')]);
  } catch (error) {
    if (!fail) throw error;
    const message = (error as Error).message;
    if (!message.startsWith('scripted ')) throw error;
    outcomes.push([key, message]);
  }
}
let actual_work_joined = false;
let source_failure_seconds = 0;
let cleanup_completed = false;
try {
  const reply = {
    commands: Object.values(commands)
      .map((command, index) => ({
        command_sequence: index + 1,
        command_id: command.command_id,
        created_at_vps_ms: now,
        child_command: command,
        model_list_request: null,
      }))
      .filter((command) => command.command_id !== 'synthetic-stop'),
  };
  const cursor = await journal!.record_poll_reply(
    stringify_pydantic_json(encode_value(reply, 'MailboxPollReply')),
    'inc',
    0n,
  );
  try {
    for await (const row of cursor) await read_canonical_body(row.command);
  } finally {
    await cursor.close();
  }
  await run('start');
  for (const raw of input.asks) {
    const ask = decode_json(raw, 'AskReference');
    child_state.install(ask);
  }
  const first = service.run(commands.prompt!);
  void first.catch(() => {});
  await entered.promise;
  // Reorder keys without parsing or rounding the high integer wire token.
  const encoded = as_object(commands.prompt!, 'ChildCommand');
  const reordered = Object.fromEntries(Object.entries(encoded).reverse());
  const second = service.run(decode_json(stringify_pydantic_json(reordered), 'ChildCommand'));
  // A transport waiter can leave; the command service never receives its cancellation signal.
  const waiter = new AbortController();
  const abandoned = Promise.race([
    first,
    new Promise<never>((_resolve, reject) => {
      waiter.signal.addEventListener('abort', () => reject(waiter.signal.reason), { once: true });
    }),
  ]);
  waiter.abort(new Error('synthetic caller disconnected'));
  await abandoned.catch(() => {});
  facts.invoked_withdraw_refused = !service.withdraw('prompt');
  release.resolve();
  outcomes.push(['prompt', as_object(await second, 'CommandOutcome')]);
  const changed = decode_json(
    stringify_pydantic_json({
      ...encoded,
      arguments: { ...(encoded.arguments as object), sender_label: 'changed' },
    }),
    'ChildCommand',
  );
  const reuse = await service.run(changed).then(
    () => null,
    (error: unknown) => error,
  );
  facts.changed_content_rejected =
    reuse instanceof Error && reuse.message === 'command id reused with different content';
  state();
  for (const key of ['reject', 'permission']) {
    await run(key, true);
    await run(key);
    state();
  }
  for (const key of ['steer-accepted', 'steer-refused', 'steer-uncertain', 'question', 'stale'])
    await run(key);
  state();
  const held = run('zero-held', true);
  void held.catch(() => {});
  await zero_entered.promise;
  const record = service.records().find((value) => value.command.command_id === 'zero-held')!;
  facts.zero_output_actual_retained =
    record.outcome !== null &&
    record.phase === 'finished' &&
    !record.has_outputs &&
    record.task !== null;
  finish_release.resolve();
  await held;
  await run('zero-held');
  const pending = service.run(commands.withdraw!);
  void pending.catch(() => {});
  await input_entered.promise;
  facts.preparation_withdrawn = service.withdraw('withdraw');
  outcomes.push(['withdraw', as_object(await pending, 'CommandOutcome')]);
  facts.prewithdrawn = service.withdraw('prewithdraw');
  await run('prewithdraw');
  await run('missing');
  const source = sources.source_work(commands.stop!.child, () => source_release.promise);
  const stop_calls = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  const stopping = run('stop');
  while (!child_state.is_retiring('child')) await yield_turn();
  const refused_source = await sources
    .source_work(commands.stop!.child, () => source_release.promise)
    .then(
      () => null,
      (error: unknown) => error,
    );
  const stop_result = await journal!.result_of('stop');
  facts.source_join_before_stop =
    refused_source instanceof Error &&
    refused_source.name === 'PermissionError' &&
    sources.count(commands.stop!.child) === 1 &&
    calls.filter((call) => JSON.stringify(call) === '["stop"]').length === stop_calls &&
    stop_result === null;
  await stop_result?.close();
  source_release.resolve();
  await source;
  await stopping;
  const synthetic = commands['synthetic-stop']!;
  const known = {
    command_id: synthetic.command_id,
    child: synthetic.child,
    disposition: 'succeeded' as const,
    result: {},
  };
  const native_calls = calls.length;
  await service.adopt_unjournaled_outcome(synthetic, known);
  await service.adopt_unjournaled_outcome(synthetic, known);
  outcomes.push([synthetic.command_id, as_object(known, 'CommandOutcome')]);
  const synthetic_result = await journal!.result_of(synthetic.command_id);
  facts.unjournaled_proof_only =
    synthetic_result === null &&
    calls.length === native_calls &&
    !trace.some((value) => {
      const row = value as unknown[];
      return ['finish', 'mark'].includes(row[0] as string) && row[1] === synthetic.command_id;
    }) &&
    store!
      .pending()
      .filter((row) => row.submission_id === 'stopped_' + digest(synthetic.command_id)).length ===
      1;
  await synthetic_result?.close();
  maintenance.begin_planned_replay();
  const cancelled = new AbortController();
  const abandoned_gate = maintenance.planned_replay_finished(cancelled.signal);
  let gate_released = false;
  const waiting_gate = maintenance
    .planned_replay_finished(new AbortController().signal)
    .then(() => {
      gate_released = true;
    });
  const disconnected = new Error('synthetic replay waiter disconnected');
  cancelled.abort(disconnected);
  const cancellation = await abandoned_gate.then(
    () => null,
    (error: unknown) => error,
  );
  const gate_held =
    cancellation instanceof ConversationStartAborted &&
    !gate_released &&
    child_state.admission_stopped;
  maintenance.finish_planned_replay();
  await waiting_gate;
  facts.shared_replay_gate =
    gate_held && child_state.admission_stopped && !maintenance.planned_update_replay;
  maintenance.resume_after_cancelled_update();
  const quiescence = await maintenance.quiesce();
  states.push(as_object(quiescence, 'Quiescence'));
  facts.receipts_block_exit = !quiescence.idle_children_stopped && store!.pending_count() > 0n;
  maintenance.resume_after_cancelled_update();
  await run('maintenance-start');
  const maintenance_child = native_children.get('maintenance-child')!;
  maintenance_child.background_task_ids.add('background');
  facts.background_blocks_idle_update =
    !(await maintenance.prepare_update('idle')) && !child_state.admission_stopped;
  maintenance_child.background_task_ids.clear();
  const updating_prompt = run('maintenance-prompt');
  void updating_prompt.catch(() => {});
  await maintenance_entered.promise;
  const stops = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  const updated = await maintenance.prepare_update('now');
  await updating_prompt;
  facts.update_stops_before_join =
    updated &&
    native_children.get('maintenance-child') === null &&
    calls.filter((call) => JSON.stringify(call) === '["stop"]').length === stops + 1;
  const update_result = await journal!.result_of('update_stop_' + update_id);
  if (update_result !== null) {
    await update_result.close();
    throw new Error('Update proof entered the command journal.');
  }
  maintenance.resume_after_cancelled_update();
  const source_failure_started = performance.now();
  await run('failed-source-start');
  const failed_reference = commands['failed-source-stop']!.child;
  const source_failure = new Error('scripted source publication failed');
  await sources
    .source_work(failed_reference, async () => {
      throw source_failure;
    })
    .catch((error: unknown) => {
      if (error !== source_failure) throw error;
    });
  const previous_stops = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  await run('failed-source-stop');
  const failed_outcome = service
    .records(failed_reference)
    .find((record) => record.command.command_id === 'failed-source-stop')!.outcome;
  facts.failed_source_withholds_proof =
    sources.failed(failed_reference) &&
    sources.errors().includes(source_failure) &&
    failed_outcome?.disposition === 'uncertain' &&
    calls.filter((call) => JSON.stringify(call) === '["stop"]').length === previous_stops + 1 &&
    store!.lookup('stopped_' + digest('failed-source-stop')) === null;
  source_failure_seconds = (performance.now() - source_failure_started) / 1000;
  await run('replay-start');
  await run('replay', true);
  facts.all_faults_observed = faults.size === 0;
  const before: string[] = [];
  const unpublished = await journal!.unpublished();
  try {
    for await (const row of unpublished) {
      before.push(row.command_id);
      await Promise.all([row.command.close(), row.result.close()]);
    }
  } finally {
    await unpublished.close();
  }
  await close();
  await open();
  const replay_publication = create_mac_output_publication(store!);
  service = new MacCommandService(
    dependencies(journal!, {
      async add(entry) {
        replay_entered.resolve();
        await replay_release.promise;
        await replay_publication.add(entry);
      },
    }),
  );
  let replay_joined = false,
    replay_was_pending = false;
  const call_count = calls.length,
    replay = await journal!.unpublished();
  try {
    for await (const row of replay) {
      try {
        const publishing = service.publish_owed_outputs(row.command, row.result);
        void publishing.catch(() => {});
        await replay_entered.promise;
        const joining = service.settled_replays().then(() => {
          replay_joined = true;
        });
        await yield_turn();
        replay_was_pending = !replay_joined;
        replay_release.resolve();
        await publishing;
        await joining;
      } finally {
        await Promise.all([row.command.close(), row.result.close()]);
      }
    }
  } finally {
    await replay.close();
  }
  const remaining = await journal!.unpublished();
  let count = 0;
  try {
    for await (const row of remaining) {
      count++;
      await Promise.all([row.command.close(), row.result.close()]);
    }
  } finally {
    await remaining.close();
  }
  facts.replay_without_native = calls.length === call_count && count === 0;
  const pending_rows = store!.pending().map((row) => ({
    submission_id: row.submission_id,
    sequence: String(row.sequence),
    conversation_id: row.conversation_id,
    kind: row.kind,
    payload: decode_json(row.payload_json, 'JsonObject').payload,
  }));
  const results: unknown[] = [];
  for (const key of Object.keys(commands)) {
    const reader = await journal!.result_of(key);
    if (reader)
      results.push([
        key,
        as_object(decode_json(await read_canonical_body(reader), 'CommandResult'), 'CommandResult'),
      ]);
  }
  await yield_turn();
  actual_work_joined =
    original_service.records().every((value) => value.task === null) &&
    failures.length === 0 &&
    replay_was_pending &&
    replay_joined &&
    sources.count() === 0;
  const unique_trace = trace.filter(
    (row, index) =>
      trace.findIndex((candidate) => JSON.stringify(candidate) === JSON.stringify(row)) === index,
  );
  const parity = decode_json(
    stringify_pydantic_json({
      calls,
      trace: unique_trace,
      outcomes,
      states,
      facts,
      unpublished_before_reopen: before,
      pending: pending_rows,
      results,
    }),
    'JsonObject',
  );
  // The semantic snapshot excludes fixture teardown. This raw join is terminal.
  const raw_source = new ShutdownDeadlineExceeded(['scripted source capability']);
  const raw_native = new ShutdownDeadlineExceeded(['scripted native capability']);
  const cleanup_stops = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  cleanup_native_failure = raw_native;
  const held_source = sources.source_work(commands.replay!.child, async () => {
    await cleanup_release.promise;
    throw raw_source;
  });
  void held_source.catch(() => {});
  let actual_settled = false;
  const actual = maintenance.settled().then(
    () => {
      actual_settled = true;
      return null;
    },
    (error: unknown) => {
      actual_settled = true;
      return error;
    },
  );
  const expired = await maintenance.stop({ deadline: performance.now() / 1000 - 1 }).then(
    () => null,
    (error: unknown) => error,
  );
  const actual_waited = !actual_settled;
  cleanup_release.resolve();
  await held_source.catch((error: unknown) => {
    if (error !== raw_source) throw error;
  });
  const outward = await maintenance.stop(options).then(
    () => null,
    (error: unknown) => error,
  );
  const actual_failure = await actual;
  const retains = (error: unknown, expected: unknown): boolean =>
    error === expected ||
    (error instanceof AggregateError &&
      error.errors.some((cause: unknown) => retains(cause, expected))) ||
    (error instanceof Error && error.cause !== undefined && retains(error.cause, expected));
  const cleanup = {
    deadline_typed:
      expired instanceof ShutdownDeadlineExceeded && expired instanceof ServerLifecycleError,
    actual_waited,
    raw_source_retained: retains(actual_failure, raw_source),
    raw_native_retained_after_retry:
      retains(actual_failure, raw_native) &&
      calls.filter((call) => JSON.stringify(call) === '["stop"]').length === cleanup_stops + 2,
    outward_typed: outward instanceof ServerLifecycleError && retains(outward, raw_native),
  };
  cleanup_completed = true;
  process.stdout.write(
    stringify_pydantic_json({
      parity,
      retry_attempts: trace,
      actual_work_joined,
      source_failure_seconds,
      cleanup,
      elapsed_seconds: (performance.now() - started) / 1000,
    }),
  );
} finally {
  release.resolve();
  finish_release.resolve();
  source_release.resolve();
  replay_release.resolve();
  maintenance_release.resolve();
  cleanup_release.resolve();
  // Teardown occurs after the semantic snapshot; it is not a command stop proof.
  try {
    if (!cleanup_completed)
      await Promise.allSettled([maintenance.stop(options), maintenance.settled()]);
    await sources.finish();
    await service.settled_replays();
    await Promise.allSettled(
      original_service.records().flatMap((record) => (record.task === null ? [] : [record.task])),
    );
  } finally {
    await close();
  }
}
