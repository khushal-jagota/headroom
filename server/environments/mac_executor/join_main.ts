/** Private stdin entry waits through join rollback before it closes process ownership. */
import { readFileSync, writeSync } from 'node:fs';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { install_mac_executor, parse_mac_join_request } from './join.ts';

const child_processes = new ChildProcesses(),
  controller = new AbortController();
const execution: ReleaseExecution = { child_processes, signal: controller.signal, deadline: null };
let interrupted: 'SIGINT' | 'SIGTERM' | undefined, failure: unknown;
function stop(signal: 'SIGINT' | 'SIGTERM'): void {
  if (interrupted) return;
  interrupted = signal;
  controller.abort(new Error(`Join interrupted by ${signal}`));
}
const interrupt = () => stop('SIGINT'),
  terminate = () => stop('SIGTERM');
process.on('SIGINT', interrupt);
process.on('SIGTERM', terminate);
try {
  if (process.argv.length !== 2) throw new Error('Join expects private JSON on stdin');
  let value: unknown;
  try {
    value = JSON.parse(readFileSync(0, 'utf8'));
  } catch {
    throw new Error('Join inputs must be valid JSON');
  }
  await install_mac_executor(parse_mac_join_request(value), execution);
} catch (error) {
  failure = error;
} finally {
  child_processes.seal();
  if (failure !== undefined) {
    try {
      child_processes.force_kill(performance.now() / 1000);
    } catch (error) {
      failure = new AggregateError([failure, error], 'Join process cleanup failed');
    }
  }
  try {
    await child_processes.settled();
  } catch (error) {
    failure =
      failure === undefined
        ? error
        : new AggregateError([failure, error], 'Join process cleanup failed');
  }
  process.off('SIGINT', interrupt);
  process.off('SIGTERM', terminate);
}
if (failure !== undefined) {
  writeSync(2, `Join refused: ${failure instanceof Error ? failure.message : String(failure)}\n`);
  process.exitCode = interrupted === 'SIGINT' ? 130 : interrupted === 'SIGTERM' ? 143 : 1;
} else process.stdout.write('Machine joined. The server accepted its credential and lease.\n');
