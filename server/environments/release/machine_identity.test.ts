import { LINUX_EXECUTOR } from './contracts.ts';
import { chmod, mkdir, mkdtemp, readFile, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { write_app_entrypoints } from './entrypoints.ts';
import { digest_machine_build } from './machine_identity.ts';
import { run_release_command } from './process.ts';

test('release provenance changes preserve identity while runtime bytes, modes and links change it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'machine-identity-'));
  const owner = new ChildProcesses();
  const execution = {
    child_processes: owner,
    signal: new AbortController().signal,
    deadline: performance.now() / 1000 + 20,
  };
  const file = async (name: string, value: string) => {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), value);
  };
  const provenance = async (sha: string) => {
    await file(
      'manifest.json',
      JSON.stringify({
        app_sha: sha,
        source_digest: sha,
        artifact_digest: sha,
        format: 'panels-app-v1',
        runtime_contract: { version: 2 },
      }),
    );
    await file(
      'mac-runtime.json',
      JSON.stringify({
        schema_version: 1,
        application_revision: sha,
        build_id: sha,
        platform: 'linux',
      }),
    );
    await file(
      'runtime/installer-files.json',
      JSON.stringify({ source_sha: sha, files: [{ path: 'entry.sh', sha256: 'bound' }] }),
    );
    await write_app_entrypoints(
      root,
      sha,
      { public_cli_entrypoint: 'entry.sh' },
      execution,
      LINUX_EXECUTOR,
    );
  };
  try {
    await file('entry.sh', '#!/bin/sh\nprintf "%s" "$PLAN_APP_SHA"\n');
    await chmod(join(root, 'entry.sh'), 0o755);
    await provenance('a'.repeat(40));
    const launcher = await readFile(join(root, 'bin/panels'), 'utf8');
    expect(launcher).not.toContain('node/bin');
    expect(await readFile(join(root, 'bin/panels-launcher'), 'utf8')).not.toContain('node/bin');
    const first = await digest_machine_build(root);
    await provenance('b'.repeat(40));
    expect(await digest_machine_build(root)).toBe(first);
    expect(await readFile(join(root, 'bin/panels'), 'utf8')).toBe(launcher);
    const result = await run_release_command(
      [join(root, 'bin/panels')],
      {
        timeout_seconds: 5,
        environment: { PATH: dirname(process.execPath) + ':/usr/bin:/bin', PLAN_APP_SHA: 'stale' },
      },
      execution,
    );
    expect(result.stdout).toBe('b'.repeat(40));
    await chmod(join(root, 'entry.sh'), 0o644);
    expect(await digest_machine_build(root)).not.toBe(first);
    await chmod(join(root, 'entry.sh'), 0o755);
    await file('entry.sh', '#!/bin/sh\nexit 0\n');
    const changed = await digest_machine_build(root);
    expect(changed).not.toBe(first);
    await symlink('entry.sh', join(root, 'current'));
    const link = await digest_machine_build(root);
    await unlink(join(root, 'current'));
    await symlink('bin/panels', join(root, 'current'));
    const changed_link = await digest_machine_build(root);
    expect(changed_link).not.toBe(link);
    await file(
      'runtime/installer-files.json',
      '{"source_sha":"ignored","files":[{"path":"entry.sh","sha256":"different"}]}',
    );
    expect(await digest_machine_build(root)).not.toBe(changed_link);
  } finally {
    owner.force_kill(performance.now() / 1000 + 5);
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
});
