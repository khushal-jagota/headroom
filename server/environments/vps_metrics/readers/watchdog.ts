/** This worker never opens SQLite. Its event loop survives its child's native SQL. */
import { createReadStream } from 'node:fs';
import { parentPort, workerData } from 'node:worker_threads';
import type { WatchdogCommand, WatchdogReply } from './contracts.ts';

const { incarnation, pid } = workerData as { incarnation: string; pid: number };
if (pid !== process.pid || !parentPort) throw new Error('invalid metrics watchdog owner');
const port = parentPort;
let active: { request: string; deadline: bigint } | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
function kill(): never {
  process.kill(pid, 'SIGKILL');
  throw new Error('metrics watchdog could not terminate its owner');
}
function check(): void {
  if (!active) return;
  const remaining = active.deadline - process.hrtime.bigint();
  if (remaining <= 0n) kill();
  timer = setTimeout(check, Math.min(2 ** 31 - 1, Math.max(1, Number(remaining) / 1e6)));
}
function reply(type: WatchdogReply['type'], request?: string): void {
  port.postMessage({ type, incarnation, ...(request === undefined ? {} : { request }) });
}
// Only the APP holds the other end. Readers and siblings never inherit that descriptor.
const liveness = createReadStream('', { fd: 4, autoClose: false });
liveness.on('end', kill);
liveness.on('error', kill);
liveness.resume();
port.on('message', (message: WatchdogCommand) => {
  if (message.incarnation !== incarnation) return;
  if (message.type === 'arm') {
    if (active) kill();
    const deadline = BigInt(message.deadline);
    if (deadline <= process.hrtime.bigint()) kill();
    active = { request: message.request, deadline };
    check();
    reply('armed', message.request);
  } else if (message.type === 'disarm') {
    if (active?.request !== message.request) return;
    if (active.deadline <= process.hrtime.bigint()) kill();
    if (timer) clearTimeout(timer);
    active = undefined;
    reply('disarmed', message.request);
  }
});
reply('ready');
