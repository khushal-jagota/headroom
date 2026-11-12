/** Own every command group until exit, including request cancellation and shutdown. */
import { spawn } from 'node:child_process';
import type { CommandOutcome } from './contracts.ts';
export interface BackendCommandOptions {
  timeout_seconds: number;
  environment_overrides?: Readonly<Record<string, string>>;
  signal?: AbortSignal;
}
export class BackendCommands {
  private readonly controller = new AbortController();
  private readonly pending = new Set<Promise<CommandOutcome>>();
  run(argv: readonly string[], options: BackendCommandOptions): Promise<CommandOutcome> {
    const signal = options.signal
      ? AbortSignal.any([options.signal, this.controller.signal])
      : this.controller.signal;
    signal.throwIfAborted();
    const operation = new Promise<CommandOutcome>((resolve, reject) => {
      const output: Buffer[] = [];
      const error: Buffer[] = [];
      let timed_out = false;
      let reaped = false;
      const child = spawn(argv[0], argv.slice(1), {
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: true,
        env: { ...process.env, ...options.environment_overrides },
      });
      const kill_group = () => {
        if (reaped || child.exitCode !== null || child.signalCode !== null) return;
        try {
          if (child.pid !== undefined) process.kill(-child.pid, 'SIGKILL');
        } catch {
          if (!reaped && child.exitCode === null && child.signalCode === null)
            child.kill('SIGKILL');
        }
      };
      const abort = () => kill_group();
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) kill_group();
      const timer = setTimeout(() => {
        timed_out = true;
        kill_group();
      }, options.timeout_seconds * 1000);
      const cleanup = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
        child.stdout.destroy();
        child.stderr.destroy();
      };
      child.stdout.on('data', (chunk: Buffer) => output.push(chunk));
      child.stderr.on('data', (chunk: Buffer) => error.push(chunk));
      child.once('error', (failure) => {
        if (child.pid === undefined) reaped = true;
        cleanup();
        if (signal.aborted) reject(signal.reason);
        else resolve({ exit_code: -1, standard_output: '', standard_error: failure.message });
      });
      child.once('exit', (code) => {
        reaped = true;
        // Descendants can retain pipes. Close our ends without signaling the
        // retired leader's numeric group, which can now be reused.
        setImmediate(() => {
          cleanup();
          if (signal.aborted) {
            reject(signal.reason);
            return;
          }
          resolve(
            timed_out
              ? {
                  exit_code: -1,
                  standard_output: '',
                  standard_error: `${argv[0]} did not answer within ${options.timeout_seconds}s`,
                }
              : {
                  exit_code: code ?? -1,
                  standard_output: Buffer.concat(output).toString('utf8'),
                  standard_error: Buffer.concat(error).toString('utf8'),
                },
          );
        });
      });
    });
    this.pending.add(operation);
    void operation.then(
      () => this.pending.delete(operation),
      () => this.pending.delete(operation),
    );
    return operation;
  }
  async shutdown(): Promise<void> {
    this.controller.abort(new Error('Backend commands stopped at shutdown'));
    await Promise.allSettled([...this.pending]);
  }
}
