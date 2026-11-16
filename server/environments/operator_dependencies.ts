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
  if (name === 'inspect' || name === 'server-event-loop-tasks') return undefined;
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
    case 'app-build': {
      const paths = await path_inputs();
      return {
        build_app: (args: import('./command_contracts.ts').OperatorArguments['app-build']) =>
          with_operation(snapshot, false, async (operation) => {
            const { build_exported_app } = await import('./release/build.ts');
            operation.require_active();
            return build_exported_app(
              paths.admit_path(paths.expand_path(args.source_root)),
              {
                requested_sha: args.requested_sha,
                candidate_app: paths.admit_path(paths.expand_path(args.candidate_app)),
                install_dependencies: true,
              },
              { public_cli_entrypoint: 'server/cli/main.ts' },
              {
                child_processes: operation.child_processes,
                signal: operation.signal,
                deadline: null,
              },
            );
          }),
      };
    }
    case 'run': {
      const paths = await path_inputs();
      return {
        async prepare_run(args: import('./command_contracts.ts').OperatorArguments['run']) {
          const { prepare_environment_run } = await import('./registry/run.ts');
          return prepare_environment_run(args, snapshot, paths);
        },
      };
    }
    case 'serve':
    case 'restart': {
      const paths = await path_inputs();
      const context = async () => {
        const { lifecycle_launch_root, resolve_unit_lifecycle_context } =
          await import('../lifecycle/context.ts');
        const inputs = { ...snapshot };
        if (inputs.PLAN_CONFIG_PATH !== undefined)
          inputs.PLAN_CONFIG_PATH = paths.admit_path(paths.expand_path(inputs.PLAN_CONFIG_PATH));
        if (inputs.PLAN_SERVER_CONTROL_SOCKET !== undefined)
          inputs.PLAN_SERVER_CONTROL_SOCKET = paths.admit_path(
            paths.expand_path(inputs.PLAN_SERVER_CONTROL_SOCKET),
          );
        const root = await lifecycle_launch_root();
        const resolved = await resolve_unit_lifecycle_context(
          { runtime_directory: '' },
          inputs,
          root,
          paths,
        );
        for (const path of [
          resolved.control_socket_path,
          resolved.lease_path,
          resolved.config.db_path,
          resolved.config.logs_dir,
          resolved.config.backup_dir,
        ])
          paths.admit_path(paths.expand_path(path));
        return { root, resolved, inputs };
      };
      if (name === 'restart')
        return {
          async request_restart() {
            const { resolved } = await context();
            const { request_control } = await import('../lifecycle/control.ts');
            await request_control(resolved.control_socket_path, {
              version: 1,
              operation: 'restart',
            });
          },
        };
      return {
        async run_supervisor() {
          const { root, resolved, inputs } = await context();
          const [{ application_launch }, { run_server_supervisor }] = await Promise.all([
            import('../lifecycle/context.ts'),
            import('../lifecycle/supervisor.ts'),
          ]);
          return run_server_supervisor({
            mode: { kind: 'foreground' },
            application: application_launch(root, inputs),
            control_socket_path: resolved.control_socket_path,
            lease_path: resolved.lease_path,
            port: resolved.port,
          });
        },
      };
    }
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
