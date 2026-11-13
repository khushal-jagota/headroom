/** Selected callbacks assemble real adapters. Importing this module creates no resources. */
import { userInfo } from 'node:os';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { OperatorInterrupted } from './command_contracts.ts';
import type {
  OperatorDependencies,
  OperatorName,
  OperatorPathInputs,
  HostEnvironmentCommandDependencies,
} from './command_contracts.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../lifecycle/contracts.ts';

export type OperatorProductionFacts<Name extends OperatorName> = undefined;
interface Operation {
  readonly child_processes: OwnedChildProcesses;
  readonly children: OwnedOneShotChildren | null;
  readonly signal: AbortSignal;
  require_active(): void;
}
async function with_operation<T>(
  environment: Readonly<NodeJS.ProcessEnv>,
  one_shots: boolean,
  action: (operation: Operation) => Promise<T>,
): Promise<T> {
  // Inert imports precede admission. Resources and their signal latch start below.
  const [{ ChildProcesses }, { OneShotChildren }, { CONTROL_TIMEOUT_SECONDS }] = await Promise.all([
    import('../lifecycle/owned_processes.ts'),
    import('../lifecycle/one_shot_children.ts'),
    import('../conversation/worker_unit/configuration.ts'),
  ]);
  const owner = new ChildProcesses();
  const native_spawn = owner.spawn.bind(owner);
  function spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
  ): ChildProcessWithoutNullStreams;
  function spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess;
  function spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess {
    return native_spawn(command, args, { ...options, env: options.env ?? environment });
  }
  owner.spawn = spawn;
  const children = one_shots ? new OneShotChildren(owner) : null;
  const controller = new AbortController();
  const cleanup_failures: unknown[] = [];
  let interrupted: { signal: 'SIGINT' | 'SIGTERM'; reason: Error } | undefined;
  const attempt = (operation: () => void) => {
    try {
      operation();
    } catch (error) {
      cleanup_failures.push(error);
    }
  };
  const interrupt = (signal: 'SIGINT' | 'SIGTERM') => {
    if (interrupted) return;
    interrupted = { signal, reason: new Error('The caller stopped the operation.') };
    const deadline = performance.now() / 1000 + CONTROL_TIMEOUT_SECONDS;
    attempt(() => owner.seal());
    attempt(() => children?.seal());
    attempt(() => owner.force_kill(deadline));
    attempt(() => controller.abort(interrupted!.reason));
  };
  const sigint = () => interrupt('SIGINT'),
    sigterm = () => interrupt('SIGTERM');
  process.on('SIGINT', sigint);
  process.on('SIGTERM', sigterm);
  let outcome: { value: T } | { error: unknown };
  try {
    try {
      outcome = {
        value: await action({
          child_processes: owner,
          children,
          signal: controller.signal,
          require_active: () => controller.signal.throwIfAborted(),
        }),
      };
    } catch (error) {
      outcome = { error };
    }
    attempt(() => owner.seal());
    attempt(() => children?.seal());
    if (!interrupted)
      attempt(() => owner.force_kill(performance.now() / 1000 + CONTROL_TIMEOUT_SECONDS));
    const settled = await Promise.allSettled([children?.settled(), owner.settled()]);
    for (const result of settled)
      if (result.status === 'rejected') cleanup_failures.push(result.reason);
  } finally {
    process.removeListener('SIGINT', sigint);
    process.removeListener('SIGTERM', sigterm);
  }
  if (interrupted) {
    const failures = [...cleanup_failures];
    if ('error' in outcome && outcome.error !== interrupted.reason) failures.push(outcome.error);
    throw new OperatorInterrupted(
      interrupted.signal,
      '',
      failures.length
        ? new AggregateError(
            [interrupted.reason, ...failures],
            'Interrupted operator work did not close cleanly',
          )
        : interrupted.reason,
    );
  }
  if (cleanup_failures.length)
    throw new AggregateError(
      'error' in outcome ? [...cleanup_failures, outcome.error] : cleanup_failures,
      'Operator children did not close cleanly',
    );
  if ('error' in outcome) throw outcome.error;
  return outcome.value;
}

export function create_production_operator_dependencies<Name extends OperatorName>(
  name: Name,
  facts: OperatorProductionFacts<Name>,
  environment: Readonly<NodeJS.ProcessEnv>,
): Promise<OperatorDependencies<Name>>;
export async function create_production_operator_dependencies(
  name: OperatorName,
  _facts: undefined,
  environment: Readonly<NodeJS.ProcessEnv>,
): Promise<OperatorDependencies<OperatorName>> {
  const snapshot = { ...environment };
  if (
    name === 'inspect' ||
    name === 'server-event-loop-tasks' ||
    name === 'app-build' ||
    name === 'run' ||
    name === 'serve' ||
    name === 'restart'
  )
    return undefined;
  const home = snapshot.HOME ?? userInfo().homedir;
  // Even path adapters load only for the selected callback, never for help/table imports.
  const path_inputs = async (): Promise<OperatorPathInputs> => {
    const [{ expand_path }, { assert_test_database_path }] = await Promise.all([
      import('./paths.ts'),
      import('../core/test_paths.ts'),
    ]);
    return {
      expand_path: (path) => expand_path(path, home),
      admit_path: (path) => assert_test_database_path(path, snapshot),
    };
  };
  switch (name) {
    case 'status':
    case 'cleanup': {
      const paths = await path_inputs();
      const { loadConfig } = await import('../core/config.ts');
      const dependencies: HostEnvironmentCommandDependencies = {
        ...paths,
        home,
        now_unix: () => Date.now() / 1000,
        load_config() {
          const config = loadConfig(
            undefined,
            { ...snapshot },
            { home, admit_path: paths.admit_path },
          );
          for (const key of ['db_path', 'logs_dir', 'backup_dir'] as const) {
            config[key] = paths.admit_path(paths.expand_path(config[key]));
          }
          return config;
        },
        with_resources: (action) =>
          with_operation(snapshot, true, async (operation) => {
            const [{ create_vps_status_dependencies }, { resolve_status_application_root }] =
              await Promise.all([
                import('./vps_status/probes.ts'),
                import('./vps_status/collect.ts'),
              ]);
            operation.require_active();
            const children = operation.children!;
            const probes = create_vps_status_dependencies(children);
            const application_root = await resolve_status_application_root(
              snapshot,
              home,
              paths.admit_path,
            );
            operation.require_active();
            return action({
              signal: operation.signal,
              require_active: operation.require_active,
              status_options: {
                application_root,
                dependencies: {
                  ...probes,
                  process_lines: () => {
                    operation.require_active();
                    return probes.process_lines();
                  },
                  git_worktree_output: (root) => {
                    operation.require_active();
                    return probes.git_worktree_output(root);
                  },
                },
              },
              scratch_options: {
                signal: operation.signal,
                admit_path: paths.admit_path,
                git_execution: { children, environment: snapshot },
              },
            });
          }),
      };
      return dependencies;
    }
    case 'backup':
    case 'restore':
    case 'prepare':
    case 'reset':
    case 'remove':
    case 'provision-skills':
      return path_inputs();
    case 'backup-current': {
      const paths = await path_inputs();
      return {
        ...paths,
        async validate_app_manifest(path: string) {
          const { validate_app_manifest } = await import('./release/identity.ts');
          await validate_app_manifest(paths.admit_path(paths.expand_path(path)));
        },
      };
    }
    case 'app-identity': {
      const dependencies: OperatorDependencies<'app-identity'> = {
        async validate_app_manifest(path, options) {
          const [{ validate_app_manifest }, paths] = await Promise.all([
            import('./release/identity.ts'),
            path_inputs(),
          ]);
          const manifest = paths.admit_path(paths.expand_path(path));
          return validate_app_manifest(manifest, options);
        },
      };
      return dependencies;
    }
    case 'watchdog':
      return {
        async run_watchdog(args) {
          const { watchdog_handler } = await import('../stalls/cli.ts');
          return watchdog_handler(args, { ...snapshot }, home);
        },
      };
  }
}
