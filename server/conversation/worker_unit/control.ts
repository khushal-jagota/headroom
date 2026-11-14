import type { OwnedChildProcesses, OwnedUnitControl } from '../../lifecycle/contracts.ts';
import type { ChildProcess } from 'node:child_process';
import { CONTROL_TIMEOUT_SECONDS } from './configuration.ts';
import { WorkerUnitError } from './contracts.ts';
import { signal_child } from '../../lifecycle/owned_processes.ts';

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
    child.stdout!.on('data', (chunk: Buffer) => output.push(chunk));
    child.stderr!.on('data', (chunk: Buffer) => errors.push(chunk));
    const timeout = AbortSignal.timeout(CONTROL_TIMEOUT_SECONDS * 1000);
    const abort = () => signal_child(child, 'SIGKILL', false);
    timeout.addEventListener('abort', abort, { once: true });
    child.once('error', (error) => {
      timeout.removeEventListener('abort', abort);
      reject(new WorkerUnitError(`Cannot contact the Worker service manager: ${error.message}`));
    });
    child.once('close', (code, signal) => {
      timeout.removeEventListener('abort', abort);
      if (timeout.aborted) {
        reject(timeout.reason);
        return;
      }
      const stdout = Buffer.concat(output);
      const stderr = Buffer.concat(errors);
      const result = code ?? (signal === 'SIGKILL' ? -9 : -1);
      resolve([result, (stdout.length ? stdout : stderr).toString('utf8').trim()]);
    });
  });
}
