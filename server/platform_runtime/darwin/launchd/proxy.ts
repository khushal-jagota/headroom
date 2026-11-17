/** Provider-facing pipes stay local; launchd owns the actual Worker job. */
import { createServer, type Server, type Socket } from 'node:net';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ChildProcesses, OneShotChildren } from '../../../lifecycle/owned_processes.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { copy_environment } from '../../../core/environment.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import { START_SECONDS, STOP_SECONDS } from '../../../worker_supervision/config.ts';
import * as records from '../../../worker_supervision/records.ts';
import { DarwinWorkerJobs } from './adapter.ts';
import { channels, type Channel, job_plist, cleanup_transport } from './transport.ts';

export async function run_proxy(record_path: string, command: readonly string[]): Promise<number> {
  const children = new ChildProcesses();
  const probes = new OneShotChildren(children);
  const jobs = new DarwinWorkerJobs({ children: probes });
  const record = await records.read(record_path);
  const job = record.launchd;
  if (!job || !command.length) throw new Error('Mac Worker proxy requires a job and command');
  const directory = await mkdtemp('/tmp/panels-worker-');
  const token = randomBytes(32).toString('hex');
  const servers: Server[] = [];
  const sockets = new Set<Socket>();
  const interrupted = Promise.withResolvers<never>();
  const failure = (error: unknown) => interrupted.reject(error);
  const stop = () => failure(new Error('Mac Worker proxy stopped'));
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
  // Every reject has an observer before startup performs asynchronous work.
  void interrupted.promise.catch(() => {});
  let admitted = false;
  let removed = false;
  try {
    const connected = new Map<Channel, Promise<Socket>>();
    for (const channel of channels) {
      const accepted = Promise.withResolvers<Socket>();
      let selected = false;
      const server = createServer({ allowHalfOpen: true }, (socket) => {
        sockets.add(socket);
        socket.once('close', () => sockets.delete(socket));
        socket.on('error', failure);
        let header = Buffer.alloc(0);
        const authenticate = (chunk: Buffer) => {
          header = Buffer.concat([header, chunk]);
          const end = header.indexOf(10);
          if (end < 0 && header.length <= 128) return;
          if (end < 0 || header.subarray(0, end).toString('utf8') !== token || selected) {
            socket.destroy();
            return;
          }
          selected = true;
          socket.pause();
          socket.off('data', authenticate);
          if (header.length > end + 1) socket.unshift(header.subarray(end + 1));
          accepted.resolve(socket);
          server.close();
        };
        socket.on('data', authenticate);
      });
      servers.push(server);
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject);
        server.listen(join(directory, channel), resolve);
      });
      connected.set(channel, accepted.promise);
    }
    const plist = join(directory, 'job.plist');
    await writeFile(
      plist,
      job_plist(
        job.label,
        [
          process.execPath,
          fileURLToPath(new URL('./entry.ts', import.meta.url)),
          record_path,
          directory,
          token,
          ...command,
        ],
        copy_environment(process.env),
        process.cwd(),
        { lower_priority: record.worker_priority !== false },
      ),
      { mode: 0o600, flag: 'wx' },
    );
    const deadline = monotonic_seconds() + START_SECONDS;
    await records.locked(record_path, deadline, async () => {
      const current = await records.read(record_path);
      if (current.launch_revoked || current.state === 'complete')
        throw new Error('Mac Worker launch authorization was revoked');
      current.launchd_socket_directory = directory;
      await records.write(record_path, current);
      admitted = true;
      // The same record lock surrounds stop/revoke, so a late bootstrap cannot
      // create a job after cleanup commits its absence receipt.
      await jobs.bootstrap(job, plist, deadline);
    });
    const ready = await observe_promise(
      Promise.race([
        Promise.all(channels.map((channel) => connected.get(channel)!)),
        interrupted.promise,
      ]),
      AbortSignal.timeout(START_SECONDS * 1000),
    );
    if (!ready.completed) throw new Error('Mac Worker transport connection deadline expired');
    const [input, output, error, control] = ready.value;
    process.stdin.pipe(input!);
    output!.pipe(process.stdout, { end: false });
    error!.pipe(process.stderr, { end: false });
    const drained = (socket: Socket): Promise<void> =>
      new Promise((resolve, reject) => {
        socket.once('end', resolve);
        socket.once('error', reject);
      });
    const outputs = Promise.all([drained(output!), drained(error!)]);
    void outputs.catch(() => {});
    const outcome = new Promise<number>((resolve, reject) => {
      let text = '';
      control!.on('data', (chunk: Buffer) => {
        text += chunk.toString('utf8');
        if (text.length > 1024) reject(new Error('Mac Worker exit response is too large'));
      });
      control!.once('end', () => {
        try {
          const value: unknown = JSON.parse(text);
          if (
            value === null ||
            typeof value !== 'object' ||
            !('code' in value) ||
            !Number.isInteger(value.code) ||
            (value.code as number) < 0 ||
            (value.code as number) > 255
          )
            throw new Error('Mac Worker exit response is invalid');
          resolve(value.code as number);
        } catch (error) {
          reject(error);
        }
      });
      control!.once('error', reject);
      control!.resume();
    });
    const code = await Promise.race([outcome, interrupted.promise]);
    // A detached marked descendant can retain the provider's stream descriptors.
    // Remove the job and sweep descendants before waiting for their output EOF.
    await records.locked(record_path, monotonic_seconds() + STOP_SECONDS, async () => {
      const current = await records.read(record_path);
      current.launch_revoked = true;
      await records.write(record_path, current);
      await jobs.stop(job, monotonic_seconds() + STOP_SECONDS);
      await cleanup_transport(directory);
      current.state = 'complete';
      current.reason = 'Mac Worker job ended and was removed';
      await records.write(record_path, current);
      removed = true;
    });
    await Promise.race([outputs, interrupted.promise]);
    return code;
  } finally {
    process.off('SIGTERM', stop);
    process.off('SIGINT', stop);
    process.stdin.unpipe();
    process.stdin.pause();
    // Preserve durable evidence if removal fails. Recovery uses this same job.
    try {
      if (admitted && !removed)
        await records.locked(record_path, monotonic_seconds() + STOP_SECONDS, async () => {
          const current = await records.read(record_path);
          current.launch_revoked = true;
          await records.write(record_path, current);
          await jobs.stop(job, monotonic_seconds() + STOP_SECONDS);
          removed = true;
        });
    } finally {
      for (const socket of sockets) socket.destroy();
      await Promise.all(
        servers.map((server) => new Promise<void>((resolve) => server.close(() => resolve()))),
      );
      probes.seal();
      await probes.settled();
      children.seal();
      await children.settled();
      if (!admitted || removed) await cleanup_transport(directory);
    }
  }
}
async function main(): Promise<void> {
  const [record, ...command] = process.argv.slice(2);
  if (!record || !command.length) throw new Error('Mac Worker proxy command is missing');
  process.exitCode = await run_proxy(record, command);
}
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url)
  await main().catch((error: unknown) => {
    console.error(error);
    process.exitCode = 1;
  });
