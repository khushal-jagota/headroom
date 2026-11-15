/** Real command/source/retirement custody and stores with scripted skills, files and backend. */
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert, { fail } from 'node:assert/strict';
import { join } from 'node:path';
import { setImmediate as yield_turn } from 'node:timers/promises';
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
import { decode_json, as_object, ExecutorValidationError } from '../../protocol.ts';
import { MacCommandService } from '../runner/service.ts';
import { create_mac_output_publication } from '../runner/publication.ts';
import type { MacCommandDependencies, MacOutputPublication } from '../runner/command_contracts.ts';
import { MacNativeChildren } from '../runner/start_stop.ts';
import { MacChildState } from '../runner/state.ts';
import { MacSourceWork } from '../runner/source_work.ts';
import { MacRetirement } from '../runner/retirement.ts';
import { MacMaintenance } from '../runner/maintenance.ts';
import { MacRunner } from '../runner/index.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import type { ChildCommand, WorkspaceIdentity } from '../../wire.generated.ts';
import { createDatabase } from '../../../core/database/index.ts';
import {
  authorize_retirement,
  has_committed_retirement,
  register_workspace,
} from '../../../ticket_workspaces/data.ts';
import { RETENTION_SECONDS } from '../../../ticket_workspaces/contracts.ts';
import type { WorkspaceState } from '../../../ticket_workspaces/contracts.ts';
import { ConversationStartAborted } from '../../../runtime/abort.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { ExecutorPaths } from '../configuration.ts';
import { MacPermissionDenied } from '../runner/contracts.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { read_canonical_body } from '../mailbox/poll.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import {
  gate,
  native_fixture_options,
  open_fixture_stores,
  close_fixture_stores,
  record_commands,
  pending_envelopes,
  fixture_cleanup_dependencies,
} from './runner_fixture.ts';

const started = performance.now(),
  root = process.argv[2]!;
const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as {
  commands: Record<string, string>;
  asks: string[];
  workspace_commands: Record<string, string>;
};

async function managed_workspace_phase(): Promise<unknown> {
  const phase_root = join(root, 'managed-proof'),
    paths = new ExecutorPaths(join(phase_root, 'state'));
  const commands = Object.fromEntries(
    Object.entries(input.workspace_commands).map(([key, raw]) => [
      key,
      decode_json(raw, 'ChildCommand'),
    ]),
  );
  const machine = '1'.repeat(32),
    calls: unknown[][] = [],
    prepared: boolean[] = [];
  let index = 0;
  for (const command of Object.values(commands)) {
    if (command.arguments.kind !== 'start') throw new Error('Managed phase expects starts');
    const ticket = new Map(
      command.arguments.resolved_start.role_materials!.identity_environment_variables,
    ).get('PLAN_TICKET_ID')!;
    const folder = join(paths.artifacts, ticket);
    mkdirSync(folder, { recursive: true });
    writeFileSync(
      join(folder, '.panels-workspace.json'),
      JSON.stringify({
        ticket_id: ticket,
        machine_id: machine,
        workspace_id: (++index).toString(16).padStart(32, '0'),
      }),
    );
  }
  writeFileSync(join(paths.root, '.workspace-machine-id'), machine);
  const entered = gate(),
    release = gate(),
    git_children = new OneShotChildren();
  const stop_entered = gate(),
    stop_release = gate(),
    source_release = gate();
  let stops = 0,
    grants = 0;
  const authority = createDatabase(join(phase_root, 'authority.sqlite'));
  const rows = (table: string) =>
    authority.read((sql) => sql.all(`SELECT * FROM ${table} ORDER BY ticket_id`));
  const stores = await open_fixture_stores(join(phase_root, 'spool'), now, on_failure, options);
  const register = async (child: ChildCommand['child'], workspace: WorkspaceIdentity) => {
    calls.push(['register', child, workspace]);
    const key = child.child_id.replace('workspace-', '');
    const start = commands[key]!.arguments;
    if (start.kind !== 'start') throw new Error('Registration expects start');
    const ticket = new Map(start.resolved_start.role_materials!.identity_environment_variables).get(
      'PLAN_TICKET_ID',
    )!;
    const folder = join(paths.artifacts, ticket);
    const marker_value = JSON.parse(readFileSync(join(folder, '.panels-workspace.json'), 'utf8'));
    prepared.push(
      marker_value.preparing === false &&
        marker_value.repository === null &&
        existsSync(join(folder, 'worktree')),
    );
    if (key === 'failure') throw new Error('scripted registration refused');
    authority.write((sql) => register_workspace(sql, ticket, child.conversation_id, workspace));
    if (key === 'one') {
      entered.resolve();
      await release.promise;
    }
    if (key === 'swap') {
      const marker = join(paths.artifacts, 't_12345678', '.panels-workspace.json');
      const value = JSON.parse(readFileSync(marker, 'utf8'));
      value.workspace_id = 'f'.repeat(32);
      writeFileSync(marker, JSON.stringify(value));
    }
  };
  const runner = new MacRunner({
    incarnation: 'inc',
    journal: stores.journal,
    outbox: stores.store,
    cleanup: fixture_cleanup_dependencies(stores.journal),
    workspace_git: { children: git_children, environment: process.env },
    register_workspace: register,
    retire_workspace: async (state, workspace) => {
      grants++;
      return authority.write((sql) =>
        authorize_retirement(sql, state, Math.floor(now / 1000), workspace),
      );
    },
    retirement_is_committed: async (state, workspace) =>
      authority.read((sql) => has_committed_retirement(sql, state, workspace)),
    inputs: {
      download_inputs: async () => {
        throw new Error('No inputs in managed phase');
      },
    },
    live: null,
    now_ms: () => now,
    new_update_id: () => {
      throw new Error('No update in managed phase');
    },
    native: native_fixture_options({
      paths,
      workspace_home: phase_root,
      native_skill_home: () => join(phase_root, 'native'),
      snapshot_directory: join(phase_root, 'skills'),
      on_failure,
      factory: (resolved) => {
        const ticket = new Map(resolved.role_materials!.identity_environment_variables).get(
          'PLAN_TICKET_ID',
        )!;
        calls.push(['factory', ticket]);
        return () =>
          ({
            async start() {
              calls.push(['start', ticket, resolved.workspace_folder.slice(phase_root.length + 1)]);
            },
            async stop() {
              stops++;
              if (stops === 1) {
                stop_entered.resolve();
                await stop_release.promise;
              }
            },
          }) as unknown as BackendChild;
      },
    }),
  });
  await record_commands(
    stores.journal,
    Object.values(commands).map((command, index) => ({
      command_id: command.command_id,
      command_sequence: index + 1,
      created_at_vps_ms: now,
      child_command: command,
      model_list_request: null,
    })),
  );
  const first = runner.run(commands.one!, new AbortController().signal);
  try {
    await entered.promise;
    let finished = false;
    void first.then(() => {
      finished = true;
    });
    const second = await runner.run(commands.two!, new AbortController().signal);
    const concurrent = !finished && second.disposition === 'succeeded';
    release.resolve();
    const outcomes = [await first, second];
    for (const key of ['failure', 'swap'])
      outcomes.push(await runner.run(commands[key]!, new AbortController().signal));
    const retained = runner.native_children
      .retained_children()
      .map((child) => child.ticket_id)
      .sort();
    const state: WorkspaceState = {
      ticket_id: 't_abcdefgh',
      ended_at: Math.floor(now / 1000) - RETENTION_SECONDS - 1,
      deleted: false,
    };
    const folder = join(paths.artifacts, state.ticket_id);
    const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
    try {
      Object.defineProperty(process, 'platform', { ...platform, value: 'darwin' });
      await runner.cleanup_workspaces([state], Math.floor(now / 1000));
      assert.equal(grants, 0, 'Darwin preservation must precede authorization');
      assert.equal(stops, 0, 'Darwin preservation must not stop a child');
      assert.ok(existsSync(folder));
    } finally {
      Object.defineProperty(process, 'platform', platform);
    }
    const source = runner.source_work(commands.one!.child, () => source_release.promise);
    await runner.cleanup_workspaces([state], Math.floor(now / 1000));
    const busy = existsSync(folder) && grants === 0 && stops === 0;
    source_release.resolve();
    await source;
    const original_append = stores.store.append.bind(stores.store);
    let fail_append = true;
    stores.store.append = (...args) => {
      if (fail_append && args[0].submission_id?.startsWith('stopped_')) {
        fail_append = false;
        throw new Error('scripted workspace output failure');
      }
      return original_append(...args);
    };
    const cleanup = runner.cleanup_workspaces([state], Math.floor(now / 1000));
    void cleanup.catch(() => {});
    await stop_entered.promise;
    const held = existsSync(folder) && rows('ticket_workspace_retirements').length === 1;
    stop_release.resolve();
    await cleanup;
    const retained_output =
      existsSync(folder) && runner.native_children.get(commands.one!.child.child_id) !== null;
    await runner.cleanup_workspaces([state], Math.floor(now / 1000));
    const unpublished = existsSync(folder) && Number(grants) === 1 && Number(stops) === 1;
    const stop = await runner.stop_and_publish({
      command_id: 'workspace_stop_' + commands.one!.child.child_id,
      child: commands.one!.child,
      operation: 'stop',
      turn: null,
      arguments: { kind: 'stop' },
    });
    assert.equal(stop.disposition, 'succeeded');
    await runner.cleanup_workspaces([state], Math.floor(now / 1000));
    const cleanup_evidence = {
      facts: [busy, held, retained_output, unpublished, !existsSync(folder)],
      grants: rows('ticket_workspace_retirements'),
      registered: rows('ticket_workspace_conversations'),
      pending: pending_envelopes(stores.store),
      stops,
    };
    return {
      calls: [...calls],
      concurrent,
      prepared,
      outcomes,
      retained,
      cleanup: cleanup_evidence,
    };
  } finally {
    release.resolve();
    stop_release.resolve();
    source_release.resolve();
    await first.catch(() => {});
    try {
      await runner.stop(options);
      await runner.settled();
    } finally {
      git_children.seal();
      try {
        await git_children.settled();
      } finally {
        try {
          await close_fixture_stores(stores, options);
        } finally {
          authority.close();
        }
      }
    }
  }
}
// Wire numbers remain inside canonical strings until the authoritative codec reads them.
const commands = Object.fromEntries(
  Object.entries(input.commands).map(([key, raw]) => [key, decode_json(raw, 'ChildCommand')]),
);
const calls: unknown[] = [],
  trace: unknown[] = [],
  outcomes: unknown[] = [],
  states: unknown[] = [];
const facts: Record<string, boolean> = {};
const guards: Record<string, boolean> = {};
const signal = new AbortController().signal;
const options = { deadline: performance.now() / 1000 + 25 };
const faults = new Set(['finish', 'append', 'mark', 'replay', 'update']);
const failures: Error[] = [];
const on_failure = (error: Error) => {
  failures.push(error);
};
const now = 1_700_000_010_123;
const update_id = '0123456789abcdef0123456789abcdef';
const entered = gate(),
  release = gate(),
  input_entered = gate(),
  zero_entered = gate(),
  finish_release = gate();
const source_release = gate(),
  replay_entered = gate(),
  replay_release = gate();
const maintenance_entered = gate(),
  maintenance_release = gate();
let maintenance_blocked = false;
let stop_failure: Error | undefined, held_stop: ReturnType<typeof gate> | undefined;
let stop_entered = gate(),
  update_ids = 0;
let store: OutboxStore, journal: AsyncCommandJournal;
async function open() {
  ({ store, journal } = await open_fixture_stores(
    join(root, 'spool'),
    Number(now),
    on_failure,
    options,
  ));
}
async function close() {
  await close_fixture_stores({ store, journal }, options);
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
    if (text.endsWith('accepted'))
      return new BackendPromptAccepted({
        composed_content_delivered: false,
        agent_acceptance_proven: true,
      });
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
    if (held_stop) {
      stop_entered.resolve();
      await held_stop.promise;
    }
    if (stop_failure) {
      const failure = stop_failure;
      stop_failure = undefined;
      throw failure;
    }
    if (maintenance_blocked) {
      maintenance_blocked = false;
      maintenance_release.resolve();
    }
  },
} as unknown as BackendChild;
mkdirSync(join(root, 'workspace'), { recursive: true });
const make_native_children = () =>
  new MacNativeChildren({
    ...native_fixture_options({
      paths: new ExecutorPaths(join(root, 'state')),
      workspace_home: root,
      factory: () => () => native,
      native_skill_home: () => join(root, 'native'),
      snapshot_directory: join(root, 'skills'),
      on_failure,
    }),
    event_sink: () => ({
      async report() {
        throw new Error('Event custody belongs to PR5.');
      },
    }),
  });
const native_children = make_native_children();
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
      if (
        entry.submission_id === 'stopped_' + digest('update_stop_' + update_id) &&
        faults.delete('update')
      )
        throw new Error('scripted update append after commit');
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
  recheck_cleanup: async () => {
    throw new Error('Explicit cleanup rechecks belong to runner cleanup proof');
  },
});
await open();
let service = new MacCommandService(
  dependencies(wrap_journal(journal!), wrap_publication(create_mac_output_publication(store!))),
);
const original_service = service;
const make_maintenance = (stop_owner = retirement) =>
  new MacMaintenance({
    native_children,
    state: child_state,
    sources,
    commands: () => service,
    outbox: { pending_count: () => store!.pending_count() },
    new_update_id: () => (update_ids++ === 0 ? update_id : 'ffffffffffffffffffffffffffffffff'),
    permanently_stopped: () => native_children.admission_stopped,
    retirement: stop_owner,
  });
const maintenance = make_maintenance();
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
try {
  {
    const recovery = make_maintenance(),
      recovery_reference = { ...commands.start!.child, child_id: 'native-recovery' };
    await native_children.start(
      recovery_reference,
      commands.start!.arguments as Parameters<MacNativeChildren['start']>[1],
      () => {},
    );
    stop_failure = new Error('scripted recoverable native stop');
    const failed = await recovery.quiesce(signal).then(
      () => null,
      (error: unknown) => error,
    );
    recovery.resume_after_cancelled_update();
    await native_children.stop_native(recovery_reference);
    native_children.retire(recovery_reference);
    await native_children.start(
      recovery_reference,
      commands.start!.arguments as Parameters<MacNativeChildren['start']>[1],
      () => {},
    );
    guards.maintenance_failure_is_recoverable =
      failed instanceof ServerLifecycleError &&
      !native_children.admission_stopped &&
      failures.length === 0 &&
      (await recovery.settled_operations()).length === 0;
    await native_children.stop_native(recovery_reference);
    native_children.retire(recovery_reference);
    const late_retirement = new MacRetirement({ native_children, state: child_state, sources });
    const abandoned = make_maintenance(late_retirement),
      cancelled = new AbortController();
    await native_children.start(
      recovery_reference,
      commands.start!.arguments as Parameters<MacNativeChildren['start']>[1],
      () => {},
    );
    held_stop = gate();
    const raw_late = new Error('scripted late native stop');
    stop_failure = raw_late;
    const late = abandoned.quiesce(cancelled.signal).then(
      () => null,
      (error: unknown) => error,
    );
    await stop_entered.promise;
    cancelled.abort();
    const cancellation = await late;
    held_stop.resolve();
    await yield_turn();
    guards.abandoned_maintenance_keeps_late_failure =
      cancellation instanceof ConversationStartAborted &&
      (await abandoned.settled_operations()).some(
        (error) => error instanceof Error && error.cause === raw_late,
      );
    abandoned.resume_after_cancelled_update();
    held_stop = undefined;
    await native_children.stop_native(recovery_reference);
    native_children.retire(recovery_reference);
    calls.length = 0;
  }
  await record_commands(
    journal!,
    Object.values(commands)
      .map((command, index) => ({
        command_sequence: index + 1,
        command_id: command.command_id,
        created_at_vps_ms: now,
        child_command: command,
        model_list_request: null,
      }))
      .filter((command) => command.command_id !== 'synthetic-stop'),
  );
  await run('start');
  const before_unsafe = calls.length;
  const unsafe = { ...commands.prompt! };
  if (unsafe.arguments.kind !== 'write_prompt')
    throw new Error('The prompt fixture must carry prompt arguments');
  unsafe.arguments = { ...unsafe.arguments, sender_message_count: Number.MAX_SAFE_INTEGER + 1 };
  const unsafe_count = await Promise.resolve()
    .then(() => service.run(unsafe))
    .catch((error: unknown) => error);
  guards.unsafe_sender_count_refused =
    unsafe_count instanceof ExecutorValidationError &&
    native_children.get('child')!.active_turn === null &&
    calls.length === before_unsafe;
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
  const second = service.run(decode_json(JSON.stringify(reordered), 'ChildCommand'));
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
    JSON.stringify({
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
  const idle_child = native_children.get('child')!,
    active_turn = idle_child.active_turn;
  idle_child.active_turn = null;
  const native_stops = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  const unsettled = await maintenance.quiesce(signal);
  guards.zero_output_fate_blocks_idle_stop =
    !record.settled &&
    record.task === null &&
    unsettled.uncertain_operation_count !== 0 &&
    calls.filter((call) => JSON.stringify(call) === '["stop"]').length === native_stops;
  const empty_children = make_native_children();
  const stop_probe = new MacRetirement({
    native_children: empty_children,
    state: new MacChildState(),
    sources,
  });
  const certificate = await stop_probe.stop_exact(commands.stop!, service, () => {});
  guards.zero_output_fate_blocks_exact_stop =
    certificate.disposition === 'uncertain' && !record.settled;
  const task_release = gate(),
    held_record = new Proxy(record, {
      get: (target, key) =>
        key === 'task'
          ? task_release.promise.then(() => record.outcome!)
          : Reflect.get(target, key),
    });
  const bounded = stop_probe
    .stop_exact(
      commands.stop!,
      {
        records: () => [held_record],
        withdraw: (id) => service.withdraw(id),
        settle_known: (reference) => service.settle_known(reference),
        prune: (reference) => service.prune(reference),
      },
      () => {},
    )
    .then(
      () => null,
      (error: unknown) => error,
    );
  await yield_turn();
  let stop_joined = false;
  const actual_stop = stop_probe.settled().then(() => {
    stop_joined = true;
  });
  stop_probe.begin_shutdown({ deadline: performance.now() / 1000 });
  guards.exact_stop_uses_original_deadline =
    (await bounded) instanceof ServerLifecycleError && !stop_joined;
  task_release.resolve();
  await actual_stop;
  await empty_children.stop(options);
  await Promise.all([empty_children.settled(), stop_probe.settled()]);
  maintenance.resume_after_cancelled_update();
  idle_child.active_turn = active_turn;
  await run('zero-held');
  const pending = service.run(commands.withdraw!);
  void pending.catch(() => {});
  await input_entered.promise;
  facts.preparation_withdrawn = service.withdraw('withdraw');
  outcomes.push(['withdraw', as_object(await pending, 'CommandOutcome')]);
  const child = native_children.get('child')!,
    previous_turn = child.active_turn,
    lock_release = gate();
  const lock = child.lock.run(() => lock_release.promise);
  await yield_turn();
  const undelivered = service.run(commands.prewithdraw!);
  await yield_turn();
  facts.prewithdrawn = service.withdraw('prewithdraw');
  lock_release.resolve();
  await lock;
  await undelivered;
  guards.withdrawn_turn_unchanged = child.active_turn === previous_turn;
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
    refused_source instanceof MacPermissionDenied &&
    sources.count(commands.stop!.child) === 1 &&
    calls.filter((call) => JSON.stringify(call) === '["stop"]').length === stop_calls &&
    stop_result === null;
  await stop_result?.close();
  source_release.resolve();
  await source;
  await stopping;
  guards.exact_retirement_prunes_records = service.records(commands.stop!.child).length === 0;
  const synthetic = commands['synthetic-stop']!;
  const known = {
    command_id: synthetic.command_id,
    child: synthetic.child,
    disposition: 'succeeded' as const,
    result: {},
  };
  const native_calls = calls.length;
  const stop_child = native_children.stop_child.bind(native_children);
  const stop_entered_once = gate(),
    stop_release_once = gate();
  let stop_attempts = 0,
    duplicate_finished = false,
    wire_finished = false;
  native_children.stop_child = async () => {
    stop_attempts++;
    stop_entered_once.resolve();
    await stop_release_once.promise;
    native_children.stop_child = stop_child;
    throw new Error('scripted stop incomplete');
  };
  const first_stop_task = retirement.stop_and_publish(synthetic, service);
  await stop_entered_once.promise;
  const duplicate_stop = retirement.stop_and_publish(synthetic, service).then((result) => {
    duplicate_finished = true;
    return result;
  });
  const competing_stop = retirement.stop_and_publish(
    { ...synthetic, command_id: 'other-stop' },
    service,
  );
  const wire_stop = retirement
    .stop_exact({ ...synthetic, command_id: 'wire-stop' }, service, () => {})
    .then((result) => {
      wire_finished = true;
      return result;
    });
  await yield_turn();
  const duplicate_waited = !duplicate_finished && !wire_finished && stop_attempts === 1;
  stop_release_once.resolve();
  const [first_stop, duplicate_result, competing_result, wire_result] = await Promise.all([
    first_stop_task,
    duplicate_stop,
    competing_stop,
    wire_stop,
  ]);
  guards.unjournaled_stop_serializes_source =
    duplicate_waited &&
    stop_attempts === 1 &&
    duplicate_result === first_stop &&
    competing_result.disposition === 'refused' &&
    wire_result.disposition === 'refused';
  guards.unjournaled_stop_can_learn_new_fate = first_stop.disposition === 'uncertain';
  await retirement.stop_and_publish(synthetic, service);
  await retirement.stop_and_publish(synthetic, service);
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
  const quiescence = await maintenance.quiesce(signal);
  states.push(as_object(quiescence, 'Quiescence'));
  facts.receipts_block_exit = !quiescence.idle_children_stopped && store!.pending_count() > 0;
  maintenance.resume_after_cancelled_update();
  await run('maintenance-start');
  const maintenance_child = native_children.get('maintenance-child')!;
  maintenance_child.background_task_ids.add('background');
  facts.background_blocks_idle_update =
    !(await maintenance.prepare_update('idle', 'idle-check', signal)) &&
    !child_state.admission_stopped;
  maintenance_child.background_task_ids.clear();
  const updating_prompt = run('maintenance-prompt');
  void updating_prompt.catch(() => {});
  await maintenance_entered.promise;
  const stops = calls.filter((call) => JSON.stringify(call) === '["stop"]').length;
  const rejected_update = await maintenance.prepare_update('now', 'planned-update', signal).then(
    () => null,
    (error: unknown) => error,
  );
  const retained_after_publication_failure =
    native_children.get(maintenance_child.reference.child_id) === maintenance_child &&
    native_children.is_retiring(maintenance_child.reference.child_id) &&
    !child_state.is_retired(maintenance_child.reference.child_id) &&
    service
      .records(maintenance_child.reference)
      .some(
        (record) => record.command.command_id === 'update_stop_' + update_id && !record.settled,
      );
  const updated = await maintenance.prepare_update('now', 'planned-update', signal);
  guards.update_retry_keeps_stop_identity =
    rejected_update instanceof Error &&
    rejected_update.message === 'scripted update append after commit' &&
    update_ids === 1;
  guards.stop_publication_retains_custody =
    retained_after_publication_failure &&
    updated &&
    native_children.get(maintenance_child.reference.child_id) === null;
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
  const pending_rows = pending_envelopes(store!);
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
  const parity = {
    calls: [...calls],
    trace: unique_trace,
    outcomes,
    states,
    facts,
    unpublished_before_reopen: before,
    pending: pending_rows,
    results,
    workspace: await managed_workspace_phase(),
  };
  const reference = { ...commands.start!.child, child_id: 'cached-native-stop' };
  const launch = commands.start!.arguments as Parameters<MacNativeChildren['start']>[1];
  const materials = launch.resolved_start.role_materials!;
  await native_children.start(
    reference,
    {
      ...launch,
      resolved_start: {
        ...launch.resolved_start,
        role_materials: {
          ...materials,
          identity_environment_variables: materials.identity_environment_variables!.map(
            ([key, value]): [string, string] => [
              key,
              key === 'PLAN_TICKET_ID' ? 'cached-stop-ticket' : value,
            ],
          ),
        },
      },
    },
    () => {},
  );
  held_stop = gate();
  stop_entered = gate();
  const native_stopping = native_children.stop_native(reference);
  await stop_entered.promise;
  const command = { ...commands.stop!, command_id: 'cached-native-stop', child: reference };
  await record_commands(journal!, [
    {
      command_sequence: Object.keys(commands).length + 1,
      command_id: command.command_id,
      created_at_vps_ms: now,
      child_command: command,
      model_list_request: null,
    },
  ]);
  const joined_stop = service.run(command);
  await yield_turn();
  guards.cached_native_stop_marks_invocation =
    service.records(reference)[0]?.phase === 'invoking' && !service.withdraw(command.command_id);
  held_stop.resolve();
  await Promise.all([native_stopping, joined_stop]);
  guards.exact_retirement_prunes_records &&= service.records(reference).length === 0;
  process.stdout.write(
    JSON.stringify({
      parity,
      retry_attempts: trace,
      actual_work_joined,
      guards,
      source_failure_seconds,
      elapsed_seconds: (performance.now() - started) / 1000,
    }),
  );
} finally {
  release.resolve();
  finish_release.resolve();
  source_release.resolve();
  replay_release.resolve();
  maintenance_release.resolve();
  held_stop?.resolve();
  // Teardown occurs after the semantic snapshot; it is not a command stop proof.
  try {
    const native_stop = native_children.stop(options);
    const operations = maintenance.finish_operations(options);
    const joined = await Promise.allSettled([
      native_stop,
      native_children.settled(),
      operations,
      maintenance.settled_operations(),
    ]);
    await sources.finish();
    await service.settled_replays();
    await Promise.allSettled(
      original_service.records().flatMap((record) => (record.task === null ? [] : [record.task])),
    );
    await retirement.settled();
    const failures = joined.flatMap((result) =>
      result.status === 'rejected'
        ? [result.reason]
        : Array.isArray(result.value)
          ? result.value
          : [],
    );
    if (failures.length) throw new AggregateError(failures, 'Command fixture cleanup failed.');
  } finally {
    await close();
  }
}
