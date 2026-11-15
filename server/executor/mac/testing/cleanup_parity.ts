/** Private snapshot proof. Synthetic observations grant no native process authority. */
import { mkdir, readFile, writeFile, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { WorkerProcess, WorkerProcessRecord } from '../../../worker_supervision/contracts.ts';
import type { CleanupCommandEvidence, CleanupSnapshotOptions } from '../cleanup_contracts.ts';
import { snapshot_cleanup } from '../cleanup_reconciliation.ts';
import { MacRunner } from '../runner/index.ts';
import { ExecutorPaths } from '../configuration.ts';
import { decode_value } from '../../protocol.ts';
import type { ChildCommand, ChildReference, StartRun } from '../../wire.generated.ts';
import { Deferred } from '../../../conversation/runtime/state.ts';
import {
  native_fixture_options,
  open_fixture_stores,
  close_fixture_stores,
  record_commands,
} from './runner_fixture.ts';
import { write as write_record } from '../../../worker_supervision/records.ts';

const began = performance.now(),
  root = process.argv[2]!;
const input = JSON.parse(await readFile(join(root, 'input.json'), 'utf8')) as {
  commands: CleanupCommandEvidence[];
  runner_start: StartRun;
  records: { name: string; record: WorkerProcessRecord; mode: number }[];
  processes: WorkerProcess[];
  steps: {
    label: string;
    options?: CleanupSnapshotOptions;
    receipt?: unknown;
    legacy?: number[];
    ended?: number[];
    readable?: string[];
  }[];
};
const run = join(root, 'run'),
  journals = join(run, 'worker-processes');
await mkdir(journals, { recursive: true, mode: 0o700 });
for (const { name, record, mode } of input.records) {
  await writeFile(join(journals, name), JSON.stringify(record), { mode });
}
const unexpected = (): never => {
  throw new Error('Unexpected native operation');
};
const fail = (error: Error): never => {
  throw error;
};
const table = new Map(input.processes.map((item) => [item.pid, item]));
let legacy = new Set<number>();
const runtime = new WorkerSupervisionRuntime({
  primitives: {
    owns_isolated_session_members: false,
    requires_containment: false,
    host_boot: async () => ({ host: 'host', boot: 'boot' }),
    snapshot: async () => new Map(table),
    is_absent: async (item) => table.get(item.pid)?.birth !== item.birth,
    signal: unexpected,
    prepare_launch: unexpected,
  },
});
const dependencies = {
  runtime,
  async *read_commands() {
    for (const [index, row] of input.commands.entries())
      yield index % 2
        ? row
        : {
            command_json: Buffer.from(row.command_json as string),
            result_json: row.result_json === null ? null : Buffer.from(row.result_json as string),
          };
  },
  files: {
    async read_runtime_record(path: string) {
      try {
        return await readFile(path, 'utf8');
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
        throw error;
      }
    },
    atomic_runtime_record: unexpected,
    remove_runtime_record: unexpected,
  },
  inspect_legacy: async (identity: { process_id: number }) => legacy.has(identity.process_id),
};
try {
  const points: unknown[] = [];
  for (const step of input.steps) {
    if (step.receipt !== undefined)
      await writeFile(join(run, 'legacy-worker-cleanup.json'), JSON.stringify(step.receipt), {
        mode: 0o600,
      });
    if (step.legacy !== undefined) legacy = new Set(step.legacy);
    for (const pid of step.ended ?? []) table.delete(pid);
    for (const name of step.readable ?? []) await chmod(join(journals, name), 0o600);
    points.push({
      label: step.label,
      ...(await snapshot_cleanup({ run }, dependencies, step.options)),
    });
  }
  const options = { deadline: performance.now() / 1000 + 10 };
  const stores = await open_fixture_stores(join(root, 'runner-spool'), 1, fail, options);
  const native_starts: string[] = [];
  const paths = new ExecutorPaths(root);
  const runner = new MacRunner({
    incarnation: 'new',
    journal: stores.journal,
    outbox: stores.store,
    cleanup: dependencies,
    now_ms: () => 1,
    new_update_id: unexpected,
    live: null,
    inputs: {
      download_inputs: unexpected,
    },
    native: native_fixture_options({
      paths,
      workspace_home: root,
      native_skill_home: () => join(root, 'native'),
      snapshot_directory: join(root, 'skills'),
      on_failure: fail,
      factory: () => () => ({
        stop_outside_lock: false,
        withdraw_delivery: unexpected,
        detach: unexpected,
        write_prompt: unexpected,
        steer: unexpected,
        cancel_running_turn: unexpected,
        answer_permission_ask: unexpected,
        answer_user_input: unexpected,
        async start(resolved: { conversation_id: string }) {
          native_starts.push(resolved.conversation_id);
        },
        async stop() {},
      }),
    }),
  });
  const template = decode_value(input.runner_start, 'StartRun');
  const start_command = (name: string, ticket: string): ChildCommand => ({
    command_id: name,
    child: { executor_incarnation: 'new', child_id: name, conversation_id: name },
    operation: 'start',
    turn: null,
    arguments: {
      ...template,
      kind: 'start',
      resolved_start: {
        ...template.resolved_start,
        conversation_id: name,
        role_materials: {
          ...template.resolved_start.role_materials!,
          identity_environment_variables: [['PLAN_TICKET_ID', ticket]],
        },
      },
    },
  });
  let sequence = 0;
  const signal = new AbortController().signal;
  async function run_command(command: ChildCommand) {
    await record_commands(stores.journal, [
      {
        command_sequence: ++sequence,
        command_id: command.command_id,
        created_at_vps_ms: 1,
        child_command: command,
      },
    ]);
    return runner.run(command, signal);
  }
  async function recheck(name: string, source: ChildReference, ticket_id: string) {
    const result = await run_command({
      command_id: name,
      child: { executor_incarnation: 'new', child_id: 'controller', conversation_id: 'controller' },
      operation: 'recheck_cleanup',
      turn: null,
      arguments: { kind: 'recheck_cleanup', source, ticket_id },
    });
    return { disposition: result.disposition, result: result.result };
  }
  try {
    await runner.refresh_worker_cleanup();
    const initial = runner.cleanup_uncertainties;
    const blocked = await run_command(start_command('blocked', 'one'));
    const clean = start_command('clean', 'clean-ticket');
    const accepted = await run_command(clean);
    const process = { pid: 40, birth: '40', parent: 1, group: 40, session: 40, zombie: false };
    table.set(40, process);
    await write_record(join(journals, 'e'.repeat(32) + '.json'), {
      ...input.records[0]!.record,
      ticket_id: 'clean-ticket',
      conversation_id: 'clean',
      source_incarnation: 'new',
      source_child_id: 'clean',
      root: process,
      processes: [process],
    });
    await runner.refresh_worker_cleanup();
    const healthy = runner.cleanup_uncertainties;
    const active = await recheck('active-recheck', clean.child, 'clean-ticket');
    const active_cache = runner.cleanup_uncertainties;
    table.delete(40);
    const complete = await recheck('complete-recheck', clean.child, 'clean-ticket');
    const missing = await recheck(
      'missing-recheck',
      { executor_incarnation: 'old', child_id: 'missing', conversation_id: 'missing' },
      'two',
    );
    const complete_cache = runner.cleanup_uncertainties;
    const refresh = runner.refresh_worker_cleanup.bind(runner);
    const entered = new Deferred<void>(),
      release = new Deferred<void>();
    const refreshes: (string | undefined)[] = [];
    let first_refresh = true;
    runner.refresh_worker_cleanup = async (options = {}) => {
      refreshes.push(options.ticket_id);
      if (options.ticket_id === 'slow-ticket' && first_refresh) {
        first_refresh = false;
        entered.resolve();
        await release.promise;
      }
      await refresh(options);
    };
    const slow = run_command(start_command('slow-first', 'slow-ticket'));
    await entered.promise;
    const second = run_command(start_command('slow-second', 'slow-ticket'));
    await run_command(start_command('fast', 'fast-ticket'));
    const serialized =
      refreshes.filter((id) => id === 'slow-ticket').length === 1 &&
      native_starts.includes('fast') &&
      !native_starts.includes('slow-first');
    release.resolve();
    const [first, duplicate] = await Promise.all([slow, second]);
    const evidence = {
      initial,
      blocked: blocked.disposition,
      accepted: accepted.disposition,
      healthy,
      active,
      active_cache,
      complete,
      missing,
      complete_cache,
      serialized,
      first: first.disposition,
      duplicate: duplicate.disposition,
      native_starts,
    };
    console.log(
      JSON.stringify({
        parity: { points, runner: evidence },
        elapsed_seconds: (performance.now() - began) / 1000,
      }),
    );
  } finally {
    await runner.stop(options);
    await runner.settled();
    await close_fixture_stores(stores, options);
  }
} finally {
  await runtime.close();
}
