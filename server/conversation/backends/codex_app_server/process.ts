/** Each child owns a process group. Exit and pipe closure are independent facts. */
import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { CHILD_KILL_WAIT_SECONDS, CHILD_SHUTDOWN_GRACE_SECONDS } from './configuration.ts';
import { CodexChildWouldNotStart } from './errors.ts';

export interface CodexProcessOptions {
  environment: Readonly<Record<string, string>>;
  working_directory: string;
}
export class OwnedCodexProcess {
  readonly child: ChildProcessWithoutNullStreams;
  readonly exited: Promise<void>;
  private stop_task: Promise<void> | null = null;
  constructor(child: ChildProcessWithoutNullStreams) {
    this.child = child;
    this.exited = new Promise<void>((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null) resolve();
      else {
        child.once('exit', () => resolve());
        child.once('error', () => resolve());
      }
    });
  }
  stop(): Promise<void> {
    if (this.stop_task !== null) return this.stop_task;
    const task = this.stop_group();
    this.stop_task = task;
    void task.catch(() => {
      this.stop_task = null;
    });
    return task;
  }
  private signal_group(signal: NodeJS.Signals): void {
    const pid = this.child.pid;
    if (pid === undefined) return;
    try {
      process.kill(process.platform === 'win32' ? pid : -pid, signal);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
    }
  }
  private async stop_group(): Promise<void> {
    this.child.stdin.end();
    this.signal_group('SIGTERM');
    await within(this.exited, CHILD_SHUTDOWN_GRACE_SECONDS);
    // A leader can exit while descendants retain its pipes. Kill the owned group too.
    this.signal_group('SIGKILL');
    if (!(await within(this.exited, CHILD_KILL_WAIT_SECONDS)))
      throw new CodexChildWouldNotStart('the codex process did not exit after SIGKILL');
    this.child.stdout.destroy();
    this.child.stderr.destroy();
    this.child.stdin.destroy();
  }
}
export async function within(task: Promise<unknown>, seconds: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      task.then(() => true),
      new Promise<boolean>((resolve) => {
        timer = setTimeout(() => resolve(false), seconds * 1000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
export async function spawn_codex_process(
  argv: readonly string[],
  options: CodexProcessOptions,
): Promise<OwnedCodexProcess> {
  if (argv.length === 0) throw new CodexChildWouldNotStart('the codex command is empty');
  let child: ChildProcessWithoutNullStreams;
  try {
    child = spawn(argv[0]!, argv.slice(1), {
      env: { ...options.environment },
      cwd: options.working_directory,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });
  } catch (error) {
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  const owned = new OwnedCodexProcess(child);
  // Install a permanent error listener before the first event can fire.
  child.stdin.on('error', () => {});
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
  } catch (error) {
    await owned.stop();
    throw new CodexChildWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  return owned;
}
