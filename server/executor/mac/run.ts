/** CLI ownership around the fixed supervisor or its versioned Mac host child. */
import { fileURLToPath } from 'node:url';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { mac_runtime_settings } from '../../core/config.ts';
import { is_value_error } from '../../core/value_error.ts';
import { CliFailure, human_error } from '../../cli/errors.ts';
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { executor_paths, validate_executor_launch } from './configuration.ts';
import { FilesService } from './files.ts';
import { run_mac_service } from './host/entry.ts';
import { supervise } from './runtime/supervisor.ts';

export async function run_executor(invocation: Invocation): Promise<CommandResult> {
  const executor_id = invocation.values.executor_id as string;
  const revision = invocation.values.application_revision as string;
  const panels_server_url = invocation.values.panels_server_url as string;
  try {
    validate_executor_launch(executor_id, revision, panels_server_url);
  } catch (error) {
    if (!is_value_error(error)) throw error;
    throw new CliFailure(human_error(invocation.command, error.message));
  }
  const runtime = mac_runtime_settings(invocation.environment);
  const paths = executor_paths(invocation.environment);
  if (runtime.child)
    return {
      exit_code: await run_mac_service(
        { paths, revision, executor_id, panels_server_url, runtime },
        invocation.environment,
      ),
      stdout: '',
      stderr: '',
    };
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
      exit_code = await supervise(
        [
          'executor',
          'run',
          '--executor-id',
          executor_id,
          '--application-revision',
          revision,
          '--panels-server-url',
          panels_server_url,
        ],
        {
          paths,
          baseline_root: fileURLToPath(new URL('../../../', import.meta.url)),
          baseline_node: process.execPath,
          environment: invocation.environment,
          child_processes,
          one_shot_children,
          files,
          signal: controller.signal,
        },
      );
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
