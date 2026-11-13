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
  symlinkSync,
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
  mkdirSync(join(root, 'unused', 'skills'), { recursive: true });
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
  let corrupt_rebuilt = false,
    corrupt_preserved = false;
  try {
    for (const snapshot of snapshots.slice(0, 3)) {
      const generation = await publisher.install_native_skill_snapshot(snapshot, options);
      report.push([relative(root, generation), tree(options.root), tree(options.native_home)]);
    }
    const generation = await publisher.install_native_skill_snapshot(snapshots[2]!, options);
    writeFileSync(join(generation, 'skills', 'panels', 'SKILL.md'), 'corrupt');
    await publisher.install_native_skill_snapshot(snapshots[2]!, options);
    corrupt_rebuilt =
      readFileSync(join(generation, 'skills', 'panels', 'SKILL.md'), 'utf8') ===
      snapshots[2]!.sources[0]!.source_text;
    const invalid = readdirSync(join(options.root, 'skills')).find((name) =>
      name.startsWith('.invalid-'),
    );
    corrupt_preserved =
      invalid !== undefined &&
      readFileSync(
        join(options.root, 'skills', invalid, 'skills', 'panels', 'SKILL.md'),
        'utf8',
      ) === 'corrupt';
    for (const snapshot of snapshots.slice(3)) {
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
  const refusals: unknown[] = [];
  for (const name of [
    'managed-directory',
    'managed-file',
    'foreign-link',
    'case-only-name',
    'absent-home',
    'relative-home',
    'linked-home',
    'linked-skills',
    'linked-state-ancestor',
    'link-loop',
  ]) {
    const fixture = join(root, 'refusals', name),
      home = join(fixture, 'provider'),
      skills = join(home, 'skills');
    mkdirSync(fixture, { recursive: true });
    let selected_home = home,
      state = join(fixture, 'state'),
      entry = '';
    if (name !== 'absent-home' && name !== 'relative-home' && name !== 'linked-home')
      mkdirSync(skills, { recursive: true });
    if (name === 'managed-directory') {
      mkdirSync(join(skills, 'panels'));
      writeFileSync(join(skills, 'panels', 'user-data'), 'preserve');
      entry = 'panels';
    }
    if (name === 'managed-file') {
      writeFileSync(join(skills, 'panels-worker'), 'preserve');
      entry = 'panels-worker';
    }
    if (name === 'foreign-link') {
      mkdirSync(join(fixture, 'foreign'));
      symlinkSync(join(fixture, 'foreign'), join(skills, 'panels'));
      entry = 'panels';
    }
    if (name === 'case-only-name') {
      symlinkSync(join(state, 'old'), join(skills, 'Panels'));
      entry = 'Panels';
    }
    if (name === 'relative-home') selected_home = 'relative-provider';
    if (name === 'linked-home') {
      mkdirSync(join(fixture, 'real-provider', 'skills'), { recursive: true });
      symlinkSync(join(fixture, 'real-provider'), home);
    }
    if (name === 'linked-skills') {
      fs.rmdirSync(skills);
      mkdirSync(join(fixture, 'foreign-skills'));
      writeFileSync(join(fixture, 'foreign-skills', 'user-data'), 'preserve');
      symlinkSync(join(fixture, 'foreign-skills'), skills);
      entry = 'skills';
    }
    if (name === 'linked-state-ancestor') {
      mkdirSync(join(fixture, 'real-state'));
      symlinkSync(join(fixture, 'real-state'), join(fixture, 'alias'));
      state = join(fixture, 'alias', 'state');
      entry = 'linked ancestor';
    }
    if (name === 'link-loop') {
      symlinkSync('user-b', join(skills, 'user-a'));
      symlinkSync('user-a', join(skills, 'user-b'));
      entry = 'loop';
    }
    const before = JSON.stringify(tree(fixture));
    const negative = new MacSkillsPublisher((error) => faults.push(error.message));
    let refused = false,
      message = '';
    try {
      await negative.install_native_skill_snapshot(snapshots[0]!, {
        root: state,
        native_home: selected_home,
      });
    } catch (error) {
      if (!(error instanceof Error) || error.constructor.name !== 'BackendSpawnFailed') throw error;
      refused = true;
      message = error.message;
    } finally {
      await negative.stop({ deadline: performance.now() / 1000 + 10 });
      await negative.settled();
    }
    refusals.push({
      case: name,
      refused,
      unchanged: JSON.stringify(tree(fixture)) === before,
      entry_named: !entry || message.includes(entry),
      message: message.replaceAll(root, '<root>'),
    });
  }
  // Linux-only witness: this child observes its own worker FD before the folder swap.
  const swap_started = performance.now(),
    swap_root = join(root, 'folder-swap'),
    swap_home = join(swap_root, 'provider'),
    swap_state = join(swap_root, 'state');
  mkdirSync(join(swap_home, 'skills'), { recursive: true });
  mkdirSync(join(swap_state, 'skills'), { recursive: true });
  mkdirSync(join(swap_root, 'replacement'));
  writeFileSync(join(swap_root, 'replacement', 'user-data'), 'preserve');
  symlinkSync(join(swap_state, 'skills', 'old', 'panels'), join(swap_home, 'skills', 'panels'));
  const swap_parent = open_directory_descriptor(join(swap_state, 'skills'));
  const swap_lock = open_relative(
    swap_parent,
    '.install.lock',
    constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW,
    0o600,
  );
  closeSync(swap_parent);
  if (!flock_descriptor(swap_lock, 'try-exclusive'))
    throw new Error('Fixture swap lock contention.');
  const swapping = new MacSkillsPublisher((error) => faults.push(error.message));
  const swap_work = swapping.install_native_skill_snapshot(snapshots[0]!, {
    root: swap_state,
    native_home: swap_home,
  });
  void swap_work.catch(() => {});
  let swap_refused = false,
    swap_unchanged = false;
  try {
    let observed = false;
    while (!observed) {
      observed = readdirSync('/proc/self/fd').some((fd) => {
        try {
          return readlinkSync('/proc/self/fd/' + fd) === join(swap_home, 'skills');
        } catch {
          return false;
        }
      });
      if (performance.now() - swap_started > 5000)
        throw new Error('Provider directory ownership witness timed out.');
      if (!observed) await pause(5);
    }
    fs.renameSync(join(swap_home, 'skills'), join(swap_home, 'retained-skills'));
    symlinkSync(join(swap_root, 'replacement'), join(swap_home, 'skills'));
    const before = JSON.stringify([tree(swap_home), tree(join(swap_root, 'replacement'))]);
    flock_descriptor(swap_lock, 'unlock');
    await swap_work.catch((error: unknown) => {
      if (!(error instanceof Error) || error.constructor.name !== 'BackendSpawnFailed') throw error;
      swap_refused = true;
    });
    swap_unchanged =
      JSON.stringify([tree(swap_home), tree(join(swap_root, 'replacement'))]) === before;
  } finally {
    closeSync(swap_lock);
    await swapping.stop({ deadline: performance.now() / 1000 + 10 });
    await swapping.settled();
  }
  const folder_swap = {
    refused: swap_refused,
    unchanged: swap_unchanged,
    elapsed_ms: performance.now() - swap_started,
  };
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
  mkdirSync(join(root, 'unused-provider', 'skills'), { recursive: true });
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
      mac_only: { corrupt_rebuilt, corrupt_preserved, refusals, folder_swap },
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
