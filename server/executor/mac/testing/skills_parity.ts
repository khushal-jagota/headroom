/** One fresh-Python flow, without a listener or a provider. */
import { createHash } from 'node:crypto';
import {
  closeSync,
  constants,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  writeFileSync,
} from 'node:fs';
import { join, relative } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { MacSkillsPublisher } from '../skills/index.ts';
import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { Worker, isMainThread, workerData, parentPort } from 'node:worker_threads';
import {
  flock_descriptor,
  open_directory_descriptor,
  open_relative,
  mkdir_relative,
  rmdir_relative,
} from '../platform.ts';
if (!isMainThread) {
  const { root, snapshot } = workerData as { root: string; snapshot: CanonicalSkillSnapshot };
  const { install_native_skill_snapshot, owns_handles, cleanup_handles } =
    await import('../skills/installation.ts');
  const parent = open_directory_descriptor(join(root, 'skills'));
  mkdir_relative(parent, 'rmdir-check');
  rmdir_relative(parent, 'rmdir-check');
  const lock = open_relative(parent, '.install.lock', constants.O_RDWR | constants.O_NOFOLLOW);
  closeSync(parent);
  if (!flock_descriptor(lock, 'try-exclusive')) throw new Error('Fixture lock contention.');
  const cancellation = new AbortController(),
    original_close = fs.closeSync;
  let close_failed = false;
  try {
    const work = install_native_skill_snapshot(
      { root, native_home: join(root, 'unused'), snapshot },
      cancellation.signal,
    );
    void work.catch(() => {});
    await pause(0);
    fs.closeSync = (fd) => {
      if (!close_failed && fs.fstatSync(fd).isDirectory()) {
        close_failed = true;
        throw new Error('injected directory close failure');
      }
      original_close(fd);
    };
    syncBuiltinESMExports();
    cancellation.abort();
    await work.catch(() => {});
    const retained = owns_handles();
    cleanup_handles();
    parentPort!.postMessage({ retained, cleared: !owns_handles(), close_failed });
  } finally {
    fs.closeSync = original_close;
    syncBuiltinESMExports();
    closeSync(lock);
  }
} else {
  const root = process.argv[2]!;
  const input = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8')) as {
    snapshots: string[];
  };
  const snapshots = input.snapshots.map((raw) => JSON.parse(raw) as CanonicalSkillSnapshot);
  function tree(path: string, prefix = ''): unknown[] {
    const result: unknown[] = [];
    for (const name of readdirSync(path).sort()) {
      const entry = join(path, name),
        key = prefix + name,
        stat = lstatSync(entry);
      if (stat.isSymbolicLink())
        result.push([key, 'link', readlinkSync(entry).replaceAll(root, '<root>')]);
      else if (stat.isDirectory()) result.push([key, 'dir'], ...tree(entry, key + '/'));
      else {
        const bytes = readFileSync(entry);
        result.push([key, bytes.length, createHash('sha256').update(bytes).digest('hex')]);
      }
    }
    return result;
  }
  const faults: string[] = [],
    publisher = new MacSkillsPublisher((error) => faults.push(error.message));
  const options = { root: join(root, 'state'), native_home: join(root, 'provider') };
  const report: unknown[] = [];
  try {
    for (const snapshot of snapshots.slice(0, 3)) {
      const generation = await publisher.install_native_skill_snapshot(snapshot, options);
      report.push([relative(root, generation), tree(options.root), tree(options.native_home)]);
    }
    const generation = await publisher.install_native_skill_snapshot(snapshots[2]!, options);
    writeFileSync(join(generation, 'skills', 'panels', 'SKILL.md'), 'corrupt');
    for (const snapshot of [snapshots[2]!, ...snapshots.slice(3)]) {
      try {
        await publisher.install_native_skill_snapshot(snapshot, options);
        throw new Error('Expected skill refusal.');
      } catch (error) {
        if (!(error instanceof Error) || error.constructor.name !== 'BackendSpawnFailed')
          throw error;
        report.push(error.message);
      }
    }
  } finally {
    await publisher.stop({ deadline: performance.now() / 1000 + 10 });
    await publisher.settled();
  }
  let stopped_refused = false;
  try {
    await publisher.install_native_skill_snapshot(snapshots[0]!, options);
  } catch {
    stopped_refused = true;
  }
  // Hold the same root-local lock and stop an admitted publication before lock acquisition.
  const blocked_root = join(root, 'blocked'),
    skills = join(blocked_root, 'skills');
  mkdirSync(skills, { recursive: true });
  const parent = open_directory_descriptor(skills);
  const lock = open_relative(
    parent,
    '.install.lock',
    constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW,
    0o600,
  );
  closeSync(parent);
  if (!flock_descriptor(lock, 'try-exclusive'))
    throw new Error('Fixture lock is unexpectedly held.');
  const blocked = new MacSkillsPublisher((error) => faults.push(error.message));
  const work = blocked.install_native_skill_snapshot(snapshots[0]!, {
    root: blocked_root,
    native_home: join(root, 'unused-provider'),
  });
  void work.catch(() => {});
  await pause(30);
  let expired = false;
  try {
    await blocked.stop({ deadline: 0 });
  } catch {
    expired = true;
  }
  await blocked.stop({ deadline: performance.now() / 1000 + 10 });
  await blocked.settled();
  const rejected = (await Promise.allSettled([work]))[0]!.status === 'rejected';
  closeSync(lock);
  const cleanup_started = performance.now();
  let close_retry: unknown;
  const cleanup_worker = new Worker(new URL(import.meta.url), {
    execArgv: [],
    workerData: { root: blocked_root, snapshot: snapshots[0]! },
  });
  await new Promise<void>((resolve, reject) => {
    cleanup_worker.on('message', (reply: unknown) => {
      close_retry = reply;
    });
    cleanup_worker.on('error', reject);
    cleanup_worker.on('exit', (code) =>
      code === 0 && close_retry ? resolve() : reject(new Error('Cleanup fixture worker failed.')),
    );
  });
  writeFileSync(
    join(root, 'result.json'),
    JSON.stringify({
      report,
      cleanup_worker_ms: performance.now() - cleanup_started,
      lifecycle: {
        stopped_refused,
        expired,
        rejected,
        faults,
        close_retry,
        blocked: readdirSync(skills),
      },
    }),
  );
}
