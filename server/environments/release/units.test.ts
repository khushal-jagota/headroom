/** Inert private payloads prove selection without importing or executing the candidate. */
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { run_release_command } from './process.ts';
import { INSTALLED_RUNTIME_CLASSIFIER_SOURCE } from './units.ts';

test('installed classifier checks exact identity and runtime version before inert selection', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-classifier-'));
  const owner = new ChildProcesses();
  const sha = 'a'.repeat(40),
    digest = 'b'.repeat(64);
  const manifest = {
    format: 'panels-app-v1',
    app_sha: sha,
    source_digest: digest,
    artifact_digest: digest,
    runtime_contract: { version: 2 } as { version: number } | null,
  };
  const file = async (name: string, text: string, mode = 0o644) => {
    const path = join(root, name);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, text, { mode });
  };
  const identity = () => file('manifest.json', JSON.stringify(manifest));
  const classify = () =>
    run_release_command(
      [process.execPath, '--input-type=module', '-e', INSTALLED_RUNTIME_CLASSIFIER_SOURCE, root],
      { timeout_seconds: 5, allow_nonzero: true },
      {
        child_processes: owner,
        signal: new AbortController().signal,
        deadline: performance.now() / 1000 + 10,
      },
    );
  try {
    for (const name of ['bin/panels', 'bin/panels-launcher'])
      await file(name, '#!/bin/sh\nexit 93\n', 0o755);
    await identity();
    await file('runtime/contract.json', '{"version":2}');
    expect(await classify()).toEqual({ code: 0, stdout: 'node-v2\n', stderr: '' });
    await file('runtime/contract.json', '{"version":2.0}');
    expect((await classify()).stderr).toContain('unsupported runtime contract version');
    await file('runtime/contract.json', '{"version":2,"system":"linux"}');
    expect((await classify()).stderr).toContain('manifest runtime contract differs from payload');
    manifest.runtime_contract = { version: 1 };
    await identity();
    expect((await classify()).stderr).toContain('unsupported runtime contract version');
    manifest.runtime_contract = null;
    await identity();
    expect((await classify()).stderr).toContain('application lacks a runtime contract');
    manifest.runtime_contract = { version: 2 };
    manifest.app_sha = sha + '\n';
    await identity();
    expect((await classify()).stderr).toContain('invalid application identity');
    manifest.app_sha = sha;
    await identity();
    await rm(join(root, 'bin/panels'));
    await symlink(process.execPath, join(root, 'bin/panels'));
    expect((await classify()).stderr).toContain('runtime file escapes application');
  } finally {
    owner.force_kill(performance.now() / 1000 + 5);
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
}, 20_000);
