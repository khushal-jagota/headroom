/** One bounded service-manager command. Every failure reaps its exact child. */
import { spawn } from 'node:child_process';
import type { ChildProcessByStdio } from 'node:child_process';
import type { Readable } from 'node:stream';
import { evidence_error } from './errors.ts';
export type LaunchServiceQuery = (
  args: readonly string[],
) => ChildProcessByStdio<null, Readable, null>;
const launch: LaunchServiceQuery = (args) =>
  spawn('systemctl', [...args], { stdio: ['ignore', 'pipe', 'ignore'], shell: false });
export function query_services(
  scope: string,
  timeout: number,
  output_bytes: number,
  execute: LaunchServiceQuery = launch,
): Promise<string> {
  const args = [
    '--' + scope,
    'show',
    '*.service',
    '--no-pager',
    '--property=Id,ActiveState,SubState,NRestarts,ControlGroup',
  ];
  return new Promise((resolve, reject) => {
    let child: ReturnType<LaunchServiceQuery>;
    try {
      child = execute(args);
    } catch (error) {
      reject(error);
      return;
    }
    const chunks: Buffer[] = [];
    let length = 0,
      failure: unknown;
    const fail = (error: unknown) => {
      if (failure !== undefined) return;
      failure = error;
      child.kill('SIGKILL');
      // A service-manager descendant can retain the write end after this child
      // exits. Closing our bounded pipe keeps that descriptor out of the drain.
      child.stdout.destroy();
    };
    const timer = setTimeout(
      () => fail(evidence_error('ETIMEDOUT', 'service query timed out')),
      timeout * 1000,
    );
    child.stdout.on('data', (chunk: Buffer) => {
      if (failure !== undefined) return;
      if (length + chunk.length > output_bytes) {
        fail(evidence_error('TypeError', 'service query exceeded output limit'));
        return;
      }
      length += chunk.length;
      chunks.push(chunk);
    });
    child.stdout.on('error', fail);
    child.on('error', fail);
    child.on('close', (code) => {
      clearTimeout(timer);
      if (failure !== undefined) reject(failure);
      else if (code !== 0) reject(evidence_error('EIO', 'service manager unavailable'));
      else resolve(Buffer.concat(chunks, length).toString('utf8'));
    });
  });
}
