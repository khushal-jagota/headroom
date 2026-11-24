/** This wrapper and its provider share launchd's process group. */
import { constants } from 'node:os';
import { pathToFileURL } from 'node:url';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { copy_environment } from '../../../core/environment.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { START_SECONDS } from '../../../worker_supervision/config.ts';
import * as records from '../../../worker_supervision/records.ts';
import { with_worker_file_mask } from '../../../worker_supervision/file_mask.ts';
import { channels, connect_channel, await_worker_admission } from './transport.ts';

async function main(): Promise<void> {
  const [record_path, directory, token, executable, ...args] = process.argv.slice(2);
  if (!record_path || !directory || !token || !executable)
    throw new Error('Mac Worker job entry requires its transport and command');
  const children = new ChildProcesses();
  let failed = false;
  const failure = (error: unknown) => {
    if (failed) return;
    failed = true;
    console.error(error);
    // Exit the launchd-owned wrapper. launchd then reaps its remaining group.
    process.exit(1);
  };
  const [input, output, error, control] = await Promise.all(
    channels.map(async (channel) => {
      const socket = await connect_channel(directory, token, channel);
      socket.on('error', failure);
      return socket;
    }),
  );
  control!.on('end', () => failure(new Error('Mac Worker proxy disconnected')));
  control!.on('close', () => failure(new Error('Mac Worker proxy disconnected')));
  await await_worker_admission(control!);
  await records.locked(record_path, monotonic_seconds() + START_SECONDS, async () => {
    const record = await records.read(record_path);
    if (!record.launchd || record.launch_revoked || record.state === 'complete')
      throw new Error('Mac Worker launch authorization was revoked');
    // Pass the authenticated socket descriptors straight to the provider. The
    // wrapper must not buffer provider output or read its input. This preserves
    // kernel backpressure and lets the proxy drain bytes after the job exits.
    const child = with_worker_file_mask(() =>
      children.spawn(executable, args, {
        stdio: [input!, output!, error!],
        env: copy_environment(process.env),
        cwd: process.cwd(),
        detached: false,
      }),
    );
    child.once('error', failure);
    child.once('exit', (code, signal) => {
      children.seal();
      input!.destroy();
      const exit_code = code ?? (signal ? 128 + (constants.signals[signal] ?? 1) : 1);
      // Shut down the shared socket write sides after the provider exits. This
      // delivers queued bytes before EOF even when a descendant retains a dup.
      // Closing just this wrapper's descriptors cannot end those inherited pipes.
      void Promise.all(
        [output!, error!].map((socket) => new Promise<void>((resolve) => socket.end(resolve))),
      ).then(() => {
        output!.destroy();
        error!.destroy();
        control!.end(JSON.stringify({ code: exit_code }), () => process.exit(exit_code));
      }, failure);
    });
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
    if (failed) throw new Error('Mac Worker transport failed before registration');
    record.state = 'running';
    record.launch_phase = 'running';
    record.reason = 'Mac Worker job started';
    await records.write(record_path, record);
  });
}
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url)
  await main().catch((error: unknown) => {
    console.error(error);
    process.exit(1);
  });
