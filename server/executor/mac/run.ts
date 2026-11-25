/** CLI ownership around the fixed supervisor or its versioned Mac host child. */
import { MachineServerLink } from '../../server_link/machine.ts';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { decode_bounded } from '../protocol.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { applicationRootFromEnvironment, mac_runtime_settings } from '../../core/config.ts';
import { ExecutorValidationError } from '../../executor/protocol_errors.ts';
import { CliFailure, human_error } from '../../cli/errors.ts';
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import {
  ExecutorPaths,
  executor_paths,
  validate_executor_launch,
  MACHINE_SERVICE_TRIAL_ENV,
  MACHINE_WORKER_APP_ENV,
} from './configuration.ts';
import { launch_server_folders } from './server_folders.ts';
import { FilesService } from './files.ts';
import { run_mac_service } from './host/entry.ts';
import { supervise } from './runtime/supervisor.ts';

export async function run_executor(invocation: Invocation): Promise<CommandResult> {
  const executor_id = invocation.values.executor_id as string;
  const revision = invocation.values.application_revision as string;
  let paths = executor_paths(invocation.environment);
  let panels_server_url: string;
  try {
    panels_server_url = new MachineServerLink(
      paths.root,
      invocation.values.panels_server_url as string,
    ).current_url;
    validate_executor_launch(executor_id, revision, panels_server_url);
    // Only the server's own machine code, with the home identity, works in the server's
    // folder. The choice is saved, so it keeps those folders after the server moves away.
    const server_folders = launch_server_folders(paths.root, executor_id, panels_server_url);
    if (server_folders !== null) paths = new ExecutorPaths(paths.root, server_folders);
  } catch (error) {
    if (!(error instanceof ExecutorValidationError) && !(error instanceof SyntaxError)) throw error;
    throw new CliFailure(human_error(invocation.command, error.message));
  }
  const args = [
    'executor',
    'run',
    '--executor-id',
    executor_id,
    '--application-revision',
    revision,
    '--panels-server-url',
    panels_server_url,
  ];
  const runtime = mac_runtime_settings(invocation.environment);
  if (runtime.child) {
    if (runtime.ready_path !== null && invocation.environment[MACHINE_SERVICE_TRIAL_ENV] !== '1') {
      const { bridge_machine_service } = await import('./runtime/machine_service_trial.ts');
      await bridge_machine_service(
        paths.root,
        applicationRootFromEnvironment(invocation.environment),
        args,
      );
    }
    if (process.platform === 'darwin' && !invocation.environment[MACHINE_WORKER_APP_ENV]) {
      const { enter_signed_runtime, wait_for_launcher_recovery, MacLauncherRecoveryBlocked } =
        await import('./runtime/launcher_trial.ts');
      try {
        await enter_signed_runtime(paths.root, invocation.environment, () =>
          wait_for_launcher_recovery(paths.root),
        );
      } catch (error) {
        if (!(error instanceof MacLauncherRecoveryBlocked)) throw error;
        // This supervised runtime cannot safely start Workers. A successful stop keeps
        // the permanent launchd job from respawning it on the same retained error.
        return { exit_code: 0, stdout: '', stderr: error.message + '\n' };
      }
    }
    let build_id: string | undefined;
    try {
      const manifest = decode_bounded(
        await readFile(
          join(applicationRootFromEnvironment(invocation.environment), 'mac-runtime.json'),
        ),
        'MacRuntimeManifest',
      );
      if (manifest.application_revision !== revision)
        throw new Error('Machine runtime provenance differs from the launch revision.');
      build_id = manifest.build_id;
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
    }
    return {
      exit_code: await run_mac_service(
        {
          paths,
          revision,
          ...(build_id === undefined ? {} : { build_id }),
          executor_id,
          panels_server_url,
          runtime,
        },
        invocation.environment,
      ),
      stdout: '',
      stderr: '',
    };
  }
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  const files = new FilesService();
  const controller = new AbortController();
  files.on_failure((error) => controller.abort(error));
  let interrupted: number | undefined;
  const interrupt = (code: number) => {
    interrupted ??= code;
    controller.abort(new DOMException('Executor invocation stopped.', 'AbortError'));
  };
  const sigint = () => interrupt(130),
    sigterm = () => interrupt(143);
  process.on('SIGINT', sigint);
  process.on('SIGTERM', sigterm);
  try {
    let exit_code: number;
    try {
      exit_code = await supervise(args, {
        paths,
        baseline_root: fileURLToPath(new URL('../../../', import.meta.url)),
        baseline_node: process.execPath,
        environment: invocation.environment,
        child_processes,
        one_shot_children,
        files,
        signal: controller.signal,
      });
    } catch (error) {
      if (interrupted === undefined || error !== controller.signal.reason) throw error;
      exit_code = interrupted;
    }
    return { exit_code: interrupted ?? exit_code, stdout: '', stderr: '' };
  } finally {
    child_processes.seal();
    one_shot_children.seal();
    try {
      const results = await Promise.allSettled([
        child_processes.settled(),
        one_shot_children.settled(),
      ]);
      try {
        await files.stop({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
      } finally {
        await files.settled();
      }
      const failures = results.flatMap((result) =>
        result.status === 'rejected' ? [result.reason] : [],
      );
      if (failures.length)
        throw new AggregateError(failures, 'Executor supervisor cleanup failed.');
    } finally {
      process.removeListener('SIGINT', sigint);
      process.removeListener('SIGTERM', sigterm);
    }
  }
}
