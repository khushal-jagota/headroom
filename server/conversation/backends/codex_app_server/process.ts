/** Each child owns a process group. Exit and pipe closure are independent facts. */
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import type { StopOptions, OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import { CHILD_KILL_WAIT_SECONDS, CHILD_SHUTDOWN_GRACE_SECONDS } from './configuration.ts';
import { CodexChildWouldNotStart } from './errors.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { OwnedProcess, type ProcessStopOptions } from '../../../lifecycle/owned_processes.ts';

export interface CodexProcessOptions {
  child_processes: OwnedChildProcesses;
  /** The caller retains startup cleanup before any spawn-event wait. */
  on_created?: (owned: OwnedCodexProcess) => void;
  environment: Readonly<Record<string, string>>;
  working_directory: string;
  /** A preflight receives EOF without changing the main child's piped stream API. */
  close_stdin?: boolean;
}
/** Provider policy around the shared native lifetime owner. */
export class OwnedCodexProcess extends OwnedProcess {
  override stop(options: Partial<ProcessStopOptions> & Partial<StopOptions> = {}): Promise<void> {
    const remaining =
      options.deadline === undefined
        ? Infinity
        : Math.max(0, options.deadline - monotonic_seconds());
    const grace_seconds = Math.min(
      options.grace_seconds ?? CHILD_SHUTDOWN_GRACE_SECONDS,
      remaining,
    );
    return super.stop({
      ...options,
      grace_seconds,
      kill_wait_seconds: Math.min(
        options.kill_wait_seconds ?? CHILD_KILL_WAIT_SECONDS,
        Math.max(0, remaining - grace_seconds),
      ),
    });
  }
}
export async function spawn_codex_process(
  argv: readonly string[],
  options: CodexProcessOptions,
): Promise<OwnedCodexProcess> {
  if (argv.length === 0) throw new CodexChildWouldNotStart('the codex command is empty');
  let child: ChildProcessWithoutNullStreams;
  try {
    child = options.child_processes.spawn(argv[0]!, argv.slice(1), {
      env: { ...options.environment },
      cwd: options.working_directory,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });
  } catch (error) {
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  const owned = new OwnedCodexProcess(child);
  options.on_created?.(owned);
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
    if (options.close_stdin) child.stdin.end();
  } catch (error) {
    if (!options.on_created) await owned.stop();
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  return owned;
}
