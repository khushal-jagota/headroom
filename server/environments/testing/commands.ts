/** Export-only comparison. The fixture owns every path and actual child. */
import { mkdir, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { loadConfig } from '../../core/config.ts';
import { operator_commands } from '../commands.ts';
import { AppManifestValidationFailure, OperatorInterrupted } from '../command_contracts.ts';
import { create_production_operator_dependencies } from '../operator_dependencies.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { remove_ticket_scratch } from '../ticket_scratch/apply.ts';
import type {
  EnvironmentCommandArguments,
  EnvironmentCommandName,
  EnvironmentCommandResult,
  EnvironmentCommandDependencies,
  HostEnvironmentCommandDependencies,
} from '../command_contracts.ts';
interface Call {
  command: EnvironmentCommandName;
  args: unknown;
  manifest?: string;
  validator_failure?: 'os' | 'value' | 'runtime' | 'unexpected';
}
async function snapshot_only_refusals(root: string): Promise<unknown> {
  const keys = ['PLAN_TEST_MODE', 'VITEST', 'PYTEST_CURRENT_TEST'] as const;
  const original = keys.map((key) => process.env[key]);
  const outside = '/refused-operator-snapshot-path';
  const home = join(root, 'snapshot-home'),
    estate = join(root, 'snapshot-estate');
  await mkdir(home);
  await mkdir(estate);
  await symlink(outside, join(estate, 'staging'));
  await symlink(outside, join(home, 'pct'));
  const admissions: { path: string; canonical: string }[] = [];
  const record = (admit: (path: string) => string) => (path: string) => {
    const canonical = admit(path);
    admissions.push({ path, canonical });
    return canonical;
  };
  try {
    process.env.PLAN_TEST_MODE = '0';
    delete process.env.VITEST;
    delete process.env.PYTEST_CURRENT_TEST;
    assert.deepEqual(
      keys.map((key) => process.env[key]),
      ['0', undefined, undefined],
    );
    const snapshot = { ...process.env, HOME: home, PLAN_TEST_MODE: '1' };
    const remove = await create_production_operator_dependencies('remove', undefined, snapshot);
    remove.admit_path = record(remove.admit_path);
    await assert.rejects(
      operator_commands.remove.handler(
        {
          kind: 'staging',
          environment_root: estate,
          repository_roots: [],
          json: false,
        },
        remove,
      ),
      /OS temp folder/,
    );
    const provision = await create_production_operator_dependencies(
      'provision-skills',
      undefined,
      snapshot,
    );
    assert.equal(provision.expand_path('~'), home);
    provision.admit_path = record(provision.admit_path);
    const refused = await operator_commands['provision-skills'].handler(
      {
        database_parent: join(root, 'runtime'),
        hermes_home: join(home, 'hermes'),
        codex_home: join(home, 'codex'),
        claude_home: outside,
      },
      provision,
    );
    assert('failure_message' in refused && refused.failure_message.includes('OS temp folder'));
    const cleanup = await create_production_operator_dependencies('cleanup', undefined, snapshot);
    assert.equal(cleanup.home, home);
    await assert.rejects(
      cleanup.with_resources((resources) =>
        remove_ticket_scratch(
          {
            repository: join(root, 'git/panels/repository'),
            folders: [join(home, 'pct')],
          },
          [{ ticket_id: 't_12345678', entries: [join(home, 'pct/t_12345678')], branches: [] }],
          new Map([['t_12345678', ['done', 0] as const]]),
          1_800_000_000,
          {
            ...resources.scratch_options,
            admit_path: record(resources.scratch_options.admit_path!),
          },
        ),
      ),
      /OS temp folder/,
    );
    return {
      actual_test_mode: false,
      snapshot_test_mode: true,
      home,
      refused: ['environment-tree', 'provision-home', 'cleanup-legacy-home'],
      admissions,
    };
  } finally {
    keys.forEach((key, index) => {
      if (original[index] === undefined) delete process.env[key];
      else process.env[key] = original[index];
    });
  }
}
async function interrupted_status(root: string): Promise<unknown> {
  const began = performance.now(),
    spawn = ChildProcesses.prototype.spawn,
    settled = ChildProcesses.prototype.settled,
    cleanup_failure = new Error('Recorded cleanup failure after actual close'),
    listeners = [process.listeners('SIGINT'), process.listeners('SIGTERM')];
  let owner: ChildProcesses | undefined, signal: AbortSignal | undefined;
  let ready = false,
    closed = false,
    later_spawns = 0,
    ready_text = '';
  function replacement(
    this: ChildProcesses,
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
  ): ChildProcessWithoutNullStreams;
  function replacement(
    this: ChildProcesses,
    command: string,
    args: readonly string[],
    options: SpawnOptions,
  ): ChildProcess;
  function replacement(
    this: ChildProcesses,
    command: string,
    _args: readonly string[],
    options: SpawnOptions,
  ): ChildProcess {
    if (command !== 'ps' || owner) {
      later_spawns++;
      throw new Error('Interrupted status attempted another physical spawn');
    }
    owner = this;
    const child = spawn.call(
      this,
      process.execPath,
      ['-e', 'process.stdout.write("ready\\n");setInterval(()=>{},1000)'],
      options,
    );
    child.once('close', () => {
      closed = true;
    });
    child.stdout!.on('data', (chunk: Buffer) => {
      ready_text += chunk.toString();
      if (ready || !ready_text.includes('\n')) return;
      ready = true;
      process.kill(process.pid, 'SIGINT');
    });
    return child;
  }
  ChildProcesses.prototype.spawn = replacement;
  ChildProcesses.prototype.settled = async function () {
    await settled.call(this);
    if (this === owner) {
      assert(closed, 'Synthetic cleanup failure must follow actual child close');
      throw cleanup_failure;
    }
  };
  try {
    const deps = await create_production_operator_dependencies('status', undefined, {
      ...process.env,
      PLAN_APP_ROOT: join(root, 'app'),
    });
    const resources = deps.with_resources.bind(deps);
    deps.with_resources = (action) =>
      resources((current) => {
        signal = current.signal;
        return action(current);
      });
    await assert.rejects(
      operator_commands.status.handler({ json: true }, deps),
      (error: unknown) => {
        assert(error instanceof OperatorInterrupted);
        assert.equal(error.signal, 'SIGINT');
        assert.equal(error.stderr_prefix, ''); // Interrupted URL forwarding remains source-only.
        assert(error.cause instanceof AggregateError);
        assert(error.cause.errors.includes(cleanup_failure));
        assert(error.cause.errors.includes(signal?.reason));
        return true;
      },
    );
    assert.equal(ready_text, 'ready\n');
    assert(ready && closed && signal?.aborted);
    assert.equal(later_spawns, 0);
  } finally {
    ChildProcesses.prototype.spawn = spawn;
    ChildProcesses.prototype.settled = settled;
    if (owner) {
      owner.seal();
      owner.force_kill(performance.now() / 1000 + 5);
      await settled.call(owner);
    }
  }
  assert.deepEqual([process.listeners('SIGINT'), process.listeners('SIGTERM')], listeners);
  return {
    ready,
    actual_closed: closed,
    cleanup_failure_retained: true,
    later_spawns,
    milliseconds: performance.now() - began,
  };
}
async function operator_probe(root: string): Promise<unknown> {
  const environment = { ...process.env, HOME: root, PLAN_TEST_MODE: '1' };
  const paths = await create_production_operator_dependencies('backup', undefined, environment);
  environment.HOME = root + '/mutated';
  assert.equal(paths.expand_path('~'), root);
  assert.throws(() => paths.admit_path('/refused-operator-test-path'), /OS temp folder/);
  const composed_callbacks: Record<string, string[]> = {};
  for (const [name, callback] of [
    ['app-build', 'build_app'],
    ['run', 'prepare_run'],
    ['serve', 'run_supervisor'],
    ['restart', 'request_restart'],
  ] as const) {
    const dependencies = await create_production_operator_dependencies(
      name,
      undefined,
      environment,
    );
    assert.ok(dependencies);
    assert.equal(typeof Object.getOwnPropertyDescriptor(dependencies, callback)?.value, 'function');
    composed_callbacks[name] = Object.keys(dependencies).sort();
  }
  for (const name of ['inspect', 'server-event-loop-tasks'] as const)
    assert.equal(
      await create_production_operator_dependencies(name, undefined, environment),
      undefined,
    );
  const identity = await create_production_operator_dependencies(
    'app-identity',
    undefined,
    environment,
  );
  await assert.rejects(identity.validate_app_manifest(join(root, 'absent/manifest.json')));
  const outside = '/refused-operator-test-path';
  await assert.rejects(
    identity.validate_app_manifest(outside + '/manifest.json'),
    /OS temp folder/,
  );
  const current = await create_production_operator_dependencies(
    'backup-current',
    undefined,
    environment,
  );
  await assert.rejects(
    operator_commands['backup-current'].handler(
      {
        source_db: join(root, 'runtime/database.sqlite'),
        backup_dir: join(root, 'backups'),
        current_app: outside,
      },
      current,
    ),
    /OS temp folder/,
  );
  const provision = {
    database_parent: join(root, 'runtime'),
    hermes_home: join(root, 'test-hermes'),
    codex_home: join(root, 'test-codex'),
    claude_home: join(root, 'test-claude'),
  };
  const provisioning = await create_production_operator_dependencies(
    'provision-skills',
    undefined,
    environment,
  );
  const linked_parent = join(root, 'linked-skill-source');
  await mkdir(linked_parent);
  await symlink(outside, join(linked_parent, 'skills'));
  const linked = await operator_commands['provision-skills'].handler(
    { ...provision, database_parent: linked_parent },
    provisioning,
  );
  assert('failure_message' in linked && linked.failure_message.includes('OS temp folder'));
  const linked_home = join(root, 'linked-skill-home');
  await mkdir(linked_home);
  await symlink(outside, join(linked_home, 'skills'));
  const destination = await operator_commands['provision-skills'].handler(
    { ...provision, hermes_home: linked_home },
    provisioning,
  );
  assert(
    'failure_message' in destination && destination.failure_message.includes('OS temp folder'),
  );
  const watchdog_args = {
    db: join(root, 'runtime/database.sqlite'),
    state_dir: join(root, 'watchdog'),
  };
  const watchdog = await create_production_operator_dependencies('watchdog', undefined, {
    ...process.env,
    PLAN_TEST_MODE: '0',
  });
  await assert.rejects(
    operator_commands.watchdog.handler(watchdog_args, watchdog),
    /watchdog provider command is unavailable in test mode/,
  );
  return {
    composed_callbacks,
    diagnostic: await Promise.all(
      [false, true].map((json) =>
        operator_commands['server-event-loop-tasks'].handler({ json }, undefined),
      ),
    ),
    watchdog: await operator_commands.watchdog.handler(watchdog_args, {
      run_watchdog: async () => 'recorded watchdog',
    }),
    unsafe_paths_refused: 4,
    snapshot_only_refusals: await snapshot_only_refusals(root),
    invocation_home: paths.expand_path('~'),
    interrupted_status: await interrupted_status(root),
  };
}
export async function command_probe(
  root: string,
  calls: readonly Call[],
  bindings: readonly { name: string; argv: string[] }[],
): Promise<unknown> {
  const database = join(root, 'runtime/database.sqlite');
  const host = await create_production_operator_dependencies('cleanup', undefined, process.env);
  const paths = await create_production_operator_dependencies('backup', undefined, process.env);
  const config = loadConfig(join(root, 'config.yaml'), {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: database,
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_APP_ROOT: join(root, 'app'),
    PLAN_APP_SHA: 'a'.repeat(40),
    PLAN_TICKET_SCRATCH_ROOT: join(root, 'git/panels'),
    PLAN_TICKET_SCRATCH_DATABASE: database,
  });
  const deps: HostEnvironmentCommandDependencies = {
    ...host,
    load_config: () => config,
    now_unix: () => 1_800_000_000,
    with_resources: (action) =>
      host.with_resources((resources) => {
        assert(resources.scratch_options.git_execution?.children instanceof OneShotChildren);
        current_owner = resources.scratch_options.git_execution.children.child_processes;
        assert.equal(resources.scratch_options.signal, resources.signal);
        return action({
          ...resources,
          status_options: {
            application_root: join(root, 'app'),
            dependencies: {
              now: () => '2026-11-05T12:00:00+00:00',
              platform_name: () => 'Darwin',
              statvfs: async () => ({
                f_frsize: 4096,
                f_blocks: 100_000_000,
                f_bavail: 60_000_000,
              }),
              process_lines: async () => [],
              git_worktree_output: async () => '',
              live_reference_paths: async () => [],
            },
          },
        });
      }),
  };
  let current_owner: import('../../lifecycle/contracts.ts').OwnedChildProcesses | undefined;
  const scratch: { closed: boolean }[] = [],
    original_spawn = ChildProcesses.prototype.spawn;
  function record_spawn(
    this: ChildProcesses,
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
  ): ChildProcessWithoutNullStreams;
  function record_spawn(
    this: ChildProcesses,
    command: string,
    args: readonly string[],
    options: SpawnOptions,
  ): ChildProcess;
  function record_spawn(
    this: ChildProcesses,
    command: string,
    args: readonly string[],
    options: SpawnOptions,
  ): ChildProcess {
    assert.equal(command, 'git');
    assert.equal(this, current_owner);
    assert.equal(options.env?.HOME, process.env.HOME);
    assert(!Object.keys(options.env ?? {}).some((key) => key.startsWith('GIT_')));
    const row = { closed: false };
    scratch.push(row);
    const child = original_spawn.call(this, command, args, options);
    child.once('close', () => {
      row.closed = true;
    });
    return child;
  }
  const results: unknown[] = [];
  ChildProcesses.prototype.spawn = record_spawn;
  try {
    for (const call of calls) {
      if (call.manifest !== undefined)
        await writeFile(join(root, 'app/manifest.json'), call.manifest);
      try {
        let result: EnvironmentCommandResult;
        if (call.command === 'status' || call.command === 'cleanup') {
          const handler = operator_commands[call.command].handler as (
            args: EnvironmentCommandArguments['cleanup'],
            deps: HostEnvironmentCommandDependencies,
          ) => Promise<EnvironmentCommandResult>;
          result = await handler(call.args as EnvironmentCommandArguments['cleanup'], deps);
        } else if (call.command === 'backup-current') {
          result = await operator_commands['backup-current'].handler(
            call.args as EnvironmentCommandArguments['backup-current'],
            {
              ...paths,
              validate_app_manifest: async () => {
                if (call.validator_failure === 'unexpected')
                  throw Object.assign(new Error('synthetic unexpected validator failure'), {
                    name: 'LookupError',
                  });
                throw new AppManifestValidationFailure(
                  call.validator_failure ?? 'value',
                  'app artifact digest does not match its manifest',
                );
              },
            },
          );
        } else {
          const handler = operator_commands[call.command].handler as (
            args: unknown,
            deps: EnvironmentCommandDependencies<EnvironmentCommandName>,
          ) => Promise<EnvironmentCommandResult>;
          result = await handler(
            call.args,
            ['backup', 'restore', 'prepare', 'reset', 'remove', 'provision-skills'].includes(
              call.command,
            )
              ? paths
              : undefined,
          );
        }
        results.push(result);
      } catch (error) {
        results.push({ thrown: (error as Error).name });
      }
    }
  } finally {
    ChildProcesses.prototype.spawn = original_spawn;
  }
  assert(scratch.length > 0 && scratch.every((row) => row.closed));
  const { run } = await import('../../cli/main.ts');
  const routing: { name: string; args: unknown }[] = [];
  assert.deepEqual(
    new Set(bindings.map((item) => item.name)),
    new Set(Object.keys(operator_commands)),
  );
  for (const { name, argv } of bindings) {
    const entry = operator_commands[name as keyof typeof operator_commands];
    const original = entry.handler;
    Reflect.set(entry, 'handler', async (args: unknown) => {
      routing.push({ name, args });
      return { exit_code: 0, stdout: 'selected\n', stderr: '' };
    });
    try {
      assert.deepEqual(await run(argv), { exit_code: 0, stdout: 'selected\n', stderr: '' });
    } finally {
      Reflect.set(entry, 'handler', original);
    }
  }
  return {
    routing,
    results: results.map((result) => {
      if (typeof result === 'object' && result !== null && 'failure_message' in result) {
        const failure = result as {
          exit_code: 1;
          stdout: '';
          failure_message: string;
          stderr_prefix: string;
        };
        return {
          exit_code: failure.exit_code,
          stdout: failure.stdout,
          stderr: failure.stderr_prefix + 'Error: ' + failure.failure_message + '\n',
        };
      }
      return result;
    }),
    raw_results: results,
    scratch_owned: {
      actual_children: scratch.length,
      all_closed: scratch.every((row) => row.closed),
    },
    operators: await operator_probe(root),
  };
}
