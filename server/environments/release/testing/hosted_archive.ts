/** Hosted-only archive operations on the driver's private real payload. */
import { lstat, stat, statfs, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { create_app_archive, extract_app_archive } from '../archive.ts';
import * as configuration from '../configuration.ts';

export async function hosted_archive(request: {
  operation: 'pack' | 'unpack';
  app: string;
  archive: string;
  destination: string;
  sha: string;
  checksum: string;
  report: string;
}): Promise<void> {
  if (process.env.GITHUB_ACTIONS !== 'true') throw new Error('Hosted archive proof only');
  const owner = new ChildProcesses();
  let stopping = false;
  let observation_failure: unknown;
  const terminate = () => {
    stopping = true;
    process.exitCode = 1;
    try {
      owner.force_kill(performance.now() / 1000 + 5);
    } catch (error) {
      observation_failure ??= error;
    }
  };
  process.on('SIGTERM', terminate);
  const evidence: Record<string, unknown> = {
    operation: request.operation,
    limits: Object.fromEntries(
      Object.entries(configuration).filter(([name]) => name.startsWith('ARCHIVE_')),
    ),
  };
  let spawned = 0,
    closed = 0,
    spool_path: string | undefined;
  let space_calls = 0,
    minimum_available: bigint | undefined;
  const spawn = owner.spawn.bind(owner);
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (
      stopping ||
      command !== process.execPath ||
      args[0] !== fileURLToPath(new URL('../archive_unpack_main.ts', import.meta.url)) ||
      !Array.isArray(options.stdio) ||
      typeof options.stdio[3] !== 'number'
    )
      throw new Error('Hosted proof refused an unexpected child');
    const specification = JSON.parse(args[1]!) as { spool_root: string };
    spool_path = join(specification.spool_root, 'expanded.tar');
    const child = spawn(command, args, options);
    spawned++;
    child.once('close', () => closed++);
    return child;
  }) as typeof owner.spawn;
  try {
    if (request.operation === 'pack')
      evidence.checksum = await create_app_archive(request.app, request.archive);
    else {
      const manifest = await extract_app_archive(
        request.archive,
        request.destination,
        request.checksum,
        request.sha,
        owner,
        async (path) => {
          const info = await statfs(path, { bigint: true });
          const available = info.bavail * info.bsize;
          space_calls++;
          minimum_available =
            minimum_available === undefined || available < minimum_available
              ? available
              : minimum_available;
          if (spool_path) {
            // The final capacity call follows validation and sees the complete spool.
            const spool = await stat(spool_path);
            Object.assign(evidence, {
              expanded_bytes: spool.size,
              spool_allocated_bytes: spool.blocks * 512,
            });
          }
          return available;
        },
      );
      if (stopping || spawned !== closed)
        throw new Error('Unpack did not settle cleanly', { cause: observation_failure });
      if (((await lstat(request.destination)).mode & 0o777) !== 0o700)
        throw new Error('Unpack output root is not private');
      Object.assign(evidence, {
        manifest,
        spawned,
        closed,
        capacity: { calls: space_calls, minimum_available: String(minimum_available) },
      });
    }
    await writeFile(request.report, JSON.stringify(evidence) + '\n', { flag: 'wx' });
  } finally {
    if (spawned !== closed) owner.force_kill(performance.now() / 1000 + 5);
    await owner.settled();
    process.off('SIGTERM', terminate);
  }
}
