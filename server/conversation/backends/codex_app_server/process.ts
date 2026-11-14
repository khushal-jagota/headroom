/** Each child owns a process group. Exit and pipe closure are independent facts. */
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { WorkerUnit } from '../../worker_unit/index.ts';
import { CHILD_KILL_WAIT_SECONDS, CHILD_SHUTDOWN_GRACE_SECONDS } from './configuration.ts';
import { CodexChildWouldNotStart } from './errors.ts';
import { OwnedProcess, type ProcessStopOptions } from '../../../lifecycle/owned_processes.ts';

export interface CodexProcessOptions {
  child_processes: OwnedChildProcesses;
  worker_unit?: WorkerUnit;
  environment: Readonly<Record<string, string>>;
  working_directory: string;
  /** A preflight receives EOF without changing the main child's piped stream API. */
  close_stdin?: boolean;
}
/** Provider policy around the shared native lifetime owner. */
export class OwnedCodexProcess extends OwnedProcess {
  override stop(options: Partial<ProcessStopOptions> = {}): Promise<void> {
    return super.stop({
      grace_seconds: CHILD_SHUTDOWN_GRACE_SECONDS,
      kill_wait_seconds: CHILD_KILL_WAIT_SECONDS,
      ...options,
    });
  }
}
export async function spawn_codex_process(
  argv: readonly string[],
  options: CodexProcessOptions,
): Promise<OwnedCodexProcess> {
  if (argv.length === 0) throw new CodexChildWouldNotStart('the codex command is empty');
  let child: ChildProcessWithoutNullStreams;
  options.worker_unit?.begin_launch();
  try {
    child = options.child_processes.spawn(argv[0]!, argv.slice(1), {
      env: { ...options.environment },
      cwd: options.working_directory,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });
  } catch (error) {
    options.worker_unit?.launch_failed_before_spawn();
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  options.worker_unit?.track_wrapper(child);
  const owned = new OwnedCodexProcess(child);
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
    if (options.close_stdin) child.stdin.end();
  } catch (error) {
    await owned.stop();
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  return owned;
}
