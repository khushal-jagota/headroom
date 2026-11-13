import type { OwnedChildProcesses, OwnedUnitControl } from '../../lifecycle/contracts.ts';
import type { ChildProcess } from 'node:child_process';
import { getSystemErrorMessage } from 'node:util';
import { CONTROL_TIMEOUT_SECONDS } from './configuration.ts';
import { WorkerUnitError } from './contracts.ts';

function native_failure(error: Error): string {
  const errno = (error as NodeJS.ErrnoException).errno;
  if (typeof errno !== 'number') return error.message;
  const message = getSystemErrorMessage(-Math.abs(errno));
  const description = message.charAt(0).toUpperCase() + message.slice(1);
  return `[Errno ${Math.abs(errno)}] ${description}: 'systemctl'`;
}

/** One exact manager command. Timeout kills only the process this call starts. */
export async function control(
  children: Pick<OwnedChildProcesses, 'spawn'>,
  ...arguments_: string[]
): Promise<readonly [number, string]> {
  return capture(() =>
    children.spawn('systemctl', ['--user', ...arguments_], {
      stdio: ['ignore', 'pipe', 'pipe'],
    }),
  );
}
export async function control_retained(
  unit: OwnedUnitControl,
  action: 'show' | 'stop' | 'reset-failed',
  ...extra: string[]
): Promise<readonly [number, string]> {
  return capture(() => unit.spawn(action, extra));
}
function capture(spawn: () => ChildProcess): Promise<readonly [number, string]> {
  return new Promise((resolve, reject) => {
    const child = spawn();
    const output: Buffer[] = [];
    const errors: Buffer[] = [];
    let timed_out = false;
    child.stdout!.on('data', (chunk: Buffer) => output.push(chunk));
    child.stderr!.on('data', (chunk: Buffer) => errors.push(chunk));
    const timer = setTimeout(() => {
      timed_out = true;
      child.kill('SIGKILL');
    }, CONTROL_TIMEOUT_SECONDS * 1000);
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(
        new WorkerUnitError(`Cannot contact the Worker service manager: ${native_failure(error)}`),
      );
    });
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      if (timed_out) {
        const error = new Error('');
        error.name = 'TimeoutError';
        reject(error);
        return;
      }
      const stdout = Buffer.concat(output);
      const stderr = Buffer.concat(errors);
      const result = code ?? (signal === 'SIGKILL' ? -9 : -1);
      resolve([result, (stdout.length ? stdout : stderr).toString('utf8').trim()]);
    });
  });
}
