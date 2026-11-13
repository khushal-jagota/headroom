/** Each child owns a process group. Exit and pipe closure are independent facts. */
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { WorkerUnit } from '../../worker_unit/index.ts';
import { CHILD_KILL_WAIT_SECONDS, CHILD_SHUTDOWN_GRACE_SECONDS } from './configuration.ts';
import { CodexChildWouldNotStart } from './errors.ts';
import { observe_promise } from '../../../core/promise_observer.ts';

export interface CodexProcessOptions {
  child_processes: OwnedChildProcesses;
  worker_unit?: WorkerUnit;
  environment: Readonly<Record<string, string>>;
  working_directory: string;
  /** A preflight receives EOF without changing the main child's piped stream API. */
  close_stdin?: boolean;
}
interface ProcessStopOptions {
  grace_seconds?: number;
  kill_wait_seconds?: number;
  preserve_output?: boolean;
}
export class OwnedCodexProcess {
  readonly child: ChildProcessWithoutNullStreams;
  readonly exited: Promise<void>;
  readonly output_failed: Promise<Error>;
  private output_failure: Error | null = null;
  private reaped: boolean;
  private stop_task: Promise<void> | null = null;
  private signal_sent = false;
  constructor(child: ChildProcessWithoutNullStreams) {
    this.child = child;
    this.reaped = child.exitCode !== null || child.signalCode !== null;
    this.output_failed = new Promise<Error>((resolve) => {
      const failed = (error: Error) => {
        if (this.output_failure !== null) return;
        this.output_failure = error;
        resolve(error);
      };
      child.stdout.on('error', failed);
      child.stderr.on('error', failed);
    });
    // A later child error is not evidence that its process exited.
    this.exited = new Promise<void>((resolve) => {
      if (this.reaped) resolve();
      let spawned = child.pid !== undefined;
      child.once('spawn', () => {
        spawned = true;
      });
      child.once('exit', () => {
        this.reaped = true;
        resolve();
      });
      child.on('error', () => {
        if (!spawned && child.pid === undefined) {
          this.reaped = true;
          resolve();
        }
      });
    });
    child.stdin.on('error', () => {});
  }
  get output_error(): Error | null {
    return this.output_failure;
  }
  get killed(): boolean {
    return this.child.killed || this.signal_sent;
  }
  stop(options: ProcessStopOptions = {}): Promise<void> {
    if (this.stop_task !== null) return this.stop_task;
    const task = this.stop_group(options);
    this.stop_task = task;
    void task.catch(() => {
      this.stop_task = null;
    });
    return task;
  }
  signal(signal: NodeJS.Signals | number = 'SIGTERM'): boolean {
    const pid = this.child.pid;
    // After the leader is reaped, its numeric PID/PGID can belong to another process.
    if (this.reaped || this.child.exitCode !== null || this.child.signalCode !== null) return false;
    try {
      const sent =
        process.platform === 'win32' || pid === undefined
          ? this.child.kill(signal)
          : process.kill(-pid, signal);
      this.signal_sent ||= sent;
      return sent;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false;
      throw error;
    }
  }
  private async stop_group({
    grace_seconds = CHILD_SHUTDOWN_GRACE_SECONDS,
    kill_wait_seconds = CHILD_KILL_WAIT_SECONDS,
    preserve_output = false,
  }: ProcessStopOptions): Promise<void> {
    this.child.stdin.end();
    this.signal('SIGTERM');
    if (!(await observe_promise(this.exited, grace_seconds * 1000)).completed) {
      this.signal('SIGKILL');
      if (!(await observe_promise(this.exited, kill_wait_seconds * 1000)).completed)
        throw new Error('Child process termination remains unconfirmed');
    }
    // Never signal a retired group, even if descendants still hold output pipes.
    if (!preserve_output) {
      this.child.stdout.destroy();
      this.child.stderr.destroy();
    }
    this.child.stdin.destroy();
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
