/** Real child closure cannot be proved by a mocked process result. */
import { once } from 'node:events';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { create as create_tar } from 'tar';
import type { ChildProcess } from 'node:child_process';
import { expect, test, vi } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { run_release_command } from './process.ts';
import { extract_git_export } from './archive.ts';
import { build_exported_app } from './build.ts';
import { validate_app_manifest } from './identity.ts';
import type { AppManifest } from './contracts.ts';

const cleanup = vi.hoisted(() => ({ after: undefined as ((path: unknown) => void) | undefined }));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    rm: async (...args: Parameters<typeof actual.rm>) => {
      await actual.rm(...args);
      cleanup.after?.(args[0]);
    },
  };
});

test.skipIf(process.platform !== 'linux')(
  'release commands join their own close and retain the original abort and deadline',
  async () => {
    const owner = new ChildProcesses(),
      controller = new AbortController();
    const native_spawn = owner.spawn.bind(owner),
      spawned = vi.spyOn(owner, 'spawn');
    const deadline = performance.now() / 1000 + 15;
    const execution = { child_processes: owner, signal: controller.signal, deadline };
    let pending: Promise<unknown> | undefined;
    const run = (script: string, allow_nonzero = false) => {
      pending = run_release_command(
        [process.execPath, '-e', script],
        { timeout_seconds: null, allow_nonzero },
        execution,
      );
      return pending;
    };
    try {
      const unrelated = owner.spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {
        stdio: 'ignore',
      });
      await once(unrelated, 'spawn');
      expect(await run('process.stdout.write("out");process.stderr.write("err")')).toEqual({
        code: 0,
        stdout: 'out',
        stderr: 'err',
      });
      expect(await run('process.exitCode=7', true)).toEqual({ code: 7, stdout: '', stderr: '' });
      const cleanup_controller = new AbortController();
      const cleanup_reason = new Error('Abort during completed command cleanup');
      cleanup.after = () => cleanup_controller.abort(cleanup_reason);
      try {
        pending = run_release_command(
          [process.execPath, '-e', 'process.stdout.write("completed")'],
          { timeout_seconds: null },
          { ...execution, signal: cleanup_controller.signal },
        );
        await expect(pending).rejects.toBe(cleanup_reason);
      } finally {
        cleanup.after = undefined;
      }
      const signals = vi.spyOn(process, 'kill');
      try {
        await expect(run('process.stderr.write("rejected");process.exitCode=7')).rejects.toThrow(
          'rejected',
        );
        expect(signals).not.toHaveBeenCalled(); // Rejection after close never signals a reaped group.
      } finally {
        signals.mockRestore();
      }
      const reason = new Error('Recorded build abort');
      spawned.mockImplementationOnce((command, args, options) => {
        const child = native_spawn(command, args, options);
        controller.abort(reason); // Abort before the caller receives its actual handle.
        return child;
      });
      await expect(run('setInterval(()=>{},1000)')).rejects.toBe(reason);
      expect((spawned.mock.results.at(-1)!.value as ChildProcess).signalCode).toBe('SIGKILL');
      expect(unrelated.exitCode).toBeNull();
      const before = spawned.mock.calls.length;
      await expect(run('throw Error("must not launch")')).rejects.toBe(reason);
      expect(spawned).toHaveBeenCalledTimes(before);
      const clock = vi.spyOn(performance, 'now').mockReturnValue((deadline + 1) * 1000);
      try {
        await expect(
          run_release_command(
            [process.execPath, '-e', 'throw Error("expired")'],
            { timeout_seconds: null },
            { ...execution, signal: new AbortController().signal },
          ),
        ).rejects.toThrow('release operation exceeded its time budget');
        expect(spawned).toHaveBeenCalledTimes(before);
      } finally {
        clock.mockRestore();
      }
    } finally {
      owner.force_kill(performance.now() / 1000 + 5);
      await pending?.catch(() => {});
      await owner.settled();
      spawned.mockRestore();
    }
  },
  20_000,
);

// The shared command case cannot prove the archive caller's own output cleanup boundary.
test('source export retains its original deadline through spool cleanup and removes output', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-source-cleanup-'));
  const owner = new ChildProcesses(),
    controller = new AbortController();
  let pending: Promise<void> | undefined, restore_clock: (() => void) | undefined;
  const deadline = performance.now() / 1000 + 10;
  try {
    await mkdir(join(root, 'source'));
    await writeFile(join(root, 'source/payload'), 'private source');
    await create_tar({ cwd: join(root, 'source'), file: join(root, 'source.tar') }, ['payload']);
    cleanup.after = () => {
      const clock = vi.spyOn(performance, 'now').mockReturnValue((deadline + 1) * 1000);
      restore_clock = () => clock.mockRestore();
      cleanup.after = undefined;
    };
    pending = extract_git_export(join(root, 'source.tar'), join(root, 'output'), {
      child_processes: owner,
      signal: controller.signal,
      deadline,
    });
    await expect(pending).rejects.toThrow('release operation exceeded its time budget');
    await expect(stat(join(root, 'output'))).rejects.toMatchObject({ code: 'ENOENT' });
  } finally {
    cleanup.after = undefined;
    restore_clock?.();
    owner.force_kill(performance.now() / 1000 + 5);
    await pending?.catch(() => {});
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
}, 15_000);

// Reuse the real source-only builder, owner, and mocked cleanup boundary in this host.
test('builder retains its deadline through final cleanup and preserves the published candidate', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-build-cleanup-'));
  const owner = new ChildProcesses();
  const execution = {
    child_processes: owner,
    signal: new AbortController().signal,
    deadline: performance.now() / 1000 + 15,
  };
  let pending: Promise<AppManifest> | undefined, restore_clock: (() => void) | undefined;
  const source = join(root, 'source'),
    candidate = join(root, 'candidate');
  try {
    await mkdir(join(source, 'server/cli'), { recursive: true });
    // Official CLI bytes are source input only. This fixture never executes them.
    await writeFile(
      join(source, 'server/cli/main.ts'),
      await readFile(new URL('../../cli/main.ts', import.meta.url)),
    );
    await chmod(join(source, 'server/cli/main.ts'), 0o755);
    for (const args of [
      ['init', '-q', source],
      ['-C', source, 'add', '.'],
      [
        '-C',
        source,
        '-c',
        'user.name=Test',
        '-c',
        'user.email=test@example.com',
        'commit',
        '-qm',
        'private source',
      ],
    ])
      await run_release_command(['git', ...args], { timeout_seconds: null }, execution);
    const sha = (
      await run_release_command(
        ['git', '-C', source, 'rev-parse', 'HEAD'],
        { timeout_seconds: null },
        execution,
      )
    ).stdout.trim();
    cleanup.after = (path) => {
      if (
        typeof path !== 'string' ||
        dirname(path) !== root ||
        !basename(path).startsWith('.candidate-')
      )
        return;
      const clock = vi.spyOn(performance, 'now').mockReturnValue((execution.deadline + 1) * 1000);
      restore_clock = () => clock.mockRestore();
      cleanup.after = undefined;
    };
    pending = build_exported_app(
      source,
      { requested_sha: sha, candidate_app: candidate, install_dependencies: false },
      { public_cli_entrypoint: 'server/cli/main.ts' },
      execution,
    );
    await expect(pending).rejects.toThrow('release operation exceeded its time budget');
    expect(
      (await validate_app_manifest(join(candidate, 'manifest.json'), { expected_sha: sha }))
        .app_sha,
    ).toBe(sha);
  } finally {
    cleanup.after = undefined;
    restore_clock?.();
    owner.force_kill(performance.now() / 1000 + 5);
    await pending?.catch(() => {});
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
}, 20_000);
