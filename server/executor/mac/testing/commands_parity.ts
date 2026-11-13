/** Real native/callback custody and stores with scripted skills, files, backend and retirement. */
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
  // This scripted exact-stop port proves invocation/output composition only. PR5 owns source joins.
  async stop_exact(command, _work, invoking) {
    child_state.begin_retirement(command.child.child_id);
    native_children.begin_retirement(command.child);
    await native_children.stop_child(command.child, invoking);
    native_children.retire(command.child);
    child_state.mark_retired(command.child);
    return {
      command_id: command.command_id,
      child: command.child,
      disposition: 'succeeded',
      result: {},
    };
  },
  now_ms: () => now,
});
await open();
let service = new MacCommandService(
  dependencies(wrap_journal(journal!), wrap_publication(create_mac_output_publication(store!))),
);
const original_service = service;
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
try {
  const reply = {
    commands: Object.values(commands).map((command, index) => ({
      command_sequence: index + 1,
      command_id: command.command_id,
      created_at_vps_ms: now,
      child_command: command,
      model_list_request: null,
    })),
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
  await run('stop');
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
  service = new MacCommandService(dependencies(journal!, create_mac_output_publication(store!)));
  const call_count = calls.length,
    replay = await journal!.unpublished();
  try {
    for await (const row of replay) {
      try {
        await service.publish_owed_outputs(row.command, row.result);
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
    original_service.records().every((value) => value.task === null) && failures.length === 0;
  const unique_trace = trace.filter(
    (row, index) =>
      trace.findIndex((candidate) => JSON.stringify(candidate) === JSON.stringify(row)) === index,
  );
  const parity = {
    calls,
    trace: unique_trace,
    outcomes,
    states,
    facts,
    unpublished_before_reopen: before,
    pending: pending_rows,
    results,
  };
  process.stdout.write(
    stringify_pydantic_json({
      parity,
      retry_attempts: trace,
      actual_work_joined,
      elapsed_seconds: (performance.now() - started) / 1000,
    }),
  );
} finally {
  release.resolve();
  finish_release.resolve();
  // Teardown occurs after the semantic snapshot; it is not a command stop proof.
  try {
    await native_children.stop(options);
    await native_children.settled();
  } finally {
    await close();
  }
}
