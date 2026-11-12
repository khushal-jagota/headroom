/** One native read at a time, protected by a separate watchdog event loop. */
import { Worker } from 'node:worker_threads';
import { createReadAccess } from '../../../core/database/index.ts';
import { Store } from '../data/store.ts';
import { encode } from '../codec.ts';
import { MetricsOperationalError } from '../contracts.ts';
import { error_wire } from '../errors.ts';
import type { ReadAccess } from '../../../core/database/contracts.ts';
import type {
  ReadJob,
  ReaderBoot,
  ReaderReply,
  WatchdogCommand,
  WatchdogReply,
} from './contracts.ts';

function reply(message: ReaderReply): void {
  process.send?.(message);
}
function deadline_check(deadline: bigint): void {
  if (process.hrtime.bigint() >= deadline)
    throw new MetricsOperationalError('VPS metrics read deadline exceeded');
}
process.once('message', async (boot: ReaderBoot) => {
  const watchdog = new Worker(new URL('./watchdog.ts', import.meta.url), {
    workerData: { incarnation: boot.incarnation, pid: process.pid },
  });
  // An unprotected reader must never survive a watchdog failure.
  watchdog.on('error', () => process.kill(process.pid, 'SIGKILL'));
  watchdog.on('exit', () => process.kill(process.pid, 'SIGKILL'));
  const waiting = new Map<string, () => void>();
  watchdog.on('message', (message: WatchdogReply) => {
    if (message.incarnation !== boot.incarnation) return;
    const key = `${message.type}:${message.request ?? ''}`;
    waiting.get(key)?.();
    waiting.delete(key);
  });
  function handshake(type: WatchdogReply['type'], command?: WatchdogCommand): Promise<void> {
    return new Promise((resolve) => {
      const request = command && 'request' in command ? command.request : '';
      waiting.set(`${type}:${request}`, resolve);
      if (command) watchdog.postMessage(command);
    });
  }
  let reads: ReadAccess | undefined;
  try {
    await handshake('ready');
    // READY precedes SQLite open so parent death also bounds connection startup.
    reads = createReadAccess(boot.path, { busyTimeoutMs: 500 });
    const store = new Store(boot.path, boot.config);
    let active = false;
    process.on('message', async (job: ReadJob) => {
      if (job.type !== 'read' || job.incarnation !== boot.incarnation) return;
      if (active) process.kill(process.pid, 'SIGKILL');
      active = true;
      const deadline = BigInt(job.deadline);
      let armed = false;
      let response: ReaderReply;
      try {
        deadline_check(deadline);
        await handshake('armed', {
          type: 'arm',
          incarnation: boot.incarnation,
          request: job.request,
          deadline: job.deadline,
        });
        armed = true;
        // Preserve the original deadline across queueing, ARM, and snapshot acquisition.
        deadline_check(deadline);
        const payload = reads!.read((sql) => {
          deadline_check(deadline);
          const operation = job.operation;
          const result =
            operation.kind === 'latest'
              ? store.latest(sql, operation.now, deadline)
              : operation.kind === 'processes'
                ? store.processes(sql, operation.now, operation.query, deadline)
                : store.history(sql, operation.query, deadline);
          const encoded = encode(result);
          deadline_check(deadline);
          return encoded;
        });
        response = { type: 'result', incarnation: boot.incarnation, request: job.request, payload };
      } catch (error) {
        response = {
          type: 'error',
          incarnation: boot.incarnation,
          request: job.request,
          error: error_wire(error),
        };
      }
      if (armed)
        await handshake('disarmed', {
          type: 'disarm',
          incarnation: boot.incarnation,
          request: job.request,
        });
      active = false;
      reply(response);
    });
    reply({ type: 'ready', incarnation: boot.incarnation });
  } catch (error) {
    console.error(error);
    reads?.close();
    process.exit(1);
  }
});
