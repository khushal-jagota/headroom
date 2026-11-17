/** One real archive inspector case protects publication identity and evidence admission. */
import { expect, test } from 'vitest';
import { mkdir, mkdtemp, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { create } from 'tar';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { hash_archive } from './archive.ts';
import { validate_mac_publication } from './mac_publication.ts';
import type { MacRuntimePublication } from './mac_publication_contracts.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';

test('Mac publication binds source, attempt, Node proof, and sealed TypeScript archive', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-publication-'));
  const owner = new ChildProcesses();
  const execution = {
    child_processes: owner,
    signal: new AbortController().signal,
    deadline: performance.now() / 1000 + 20,
  };
  const source: PublicationSourceRun = {
    repository: 'owner/repo',
    source_sha: 'a'.repeat(40),
    event: 'push',
    ref: 'refs/heads/main',
    workflow_path: '.github/workflows/publish-typescript-release.yml',
    workflow_ref: 'owner/repo/.github/workflows/publish-typescript-release.yml@refs/heads/main',
    run_id: '123',
    run_attempt: '2',
  };
  try {
    const runtime = join(root, 'runtime');
    await mkdir(join(runtime, 'node/bin'), { recursive: true });
    await mkdir(join(runtime, 'server/executor/mac'), { recursive: true });
    await writeFile(join(runtime, 'node/bin/node'), 'structural executable', { mode: 0o755 });
    await writeFile(join(runtime, 'server/executor/mac/entrypoint.ts'), '// structural entrypoint');
    await writeFile(
      join(runtime, 'mac-runtime.json'),
      JSON.stringify({
        schema_version: 1,
        application_revision: source.source_sha,
        platform: 'darwin',
        architecture: 'arm64',
      }),
    );
    const archive = join(root, 'panels-mac-runtime.tar.gz');
    await create({ cwd: runtime, file: archive, gzip: true }, [
      'node',
      'server',
      'mac-runtime.json',
    ]);
    const receipt: MacRuntimePublication = {
      schema_version: 1,
      application_revision: source.source_sha,
      platform: 'darwin',
      architecture: 'arm64',
      publication_run_id: source.run_id,
      run_attempt: source.run_attempt,
      archive_sha256: await hash_archive(archive),
      archive_size: (await stat(archive)).size,
      node_version: '22.18.0',
      node_sha256: 'b'.repeat(64),
      proof: {
        relocated_archive: 'passed',
        executor_cli: 'passed',
        native_files: 'passed',
        sqlite: 'passed',
        host_import: 'passed',
        duration_seconds: 1,
      },
    };
    const save = (value: unknown) =>
      writeFile(join(root, 'mac-runtime-publication.json'), JSON.stringify(value));
    await save(receipt);
    await expect(
      validate_mac_publication(root, source, 456, 'success', execution),
    ).resolves.toEqual({
      artifact_id: 456,
      archive_sha256: receipt.archive_sha256,
      archive_size: receipt.archive_size,
    });
    // These independent identity inputs all come from the existing receipt fixture.
    for (const change of [
      { application_revision: 'c'.repeat(40) },
      { publication_run_id: '122' },
      { run_attempt: '1' },
      { archive_sha256: 'd'.repeat(64) },
      { archive_size: receipt.archive_size + 1 },
      { node_version: '22.17.0' },
      { proof: { ...receipt.proof, native_files: 'failed' } },
      { proof: { ...receipt.proof, relocated_archive: 'failed' } },
    ]) {
      await save({ ...receipt, ...change });
      await expect(
        validate_mac_publication(root, source, 456, 'success', execution),
      ).rejects.toThrow('Mac publication differs');
    }
    await save(receipt);
    await expect(validate_mac_publication(root, source, 456, 'failure', execution)).rejects.toThrow(
      'successful native build',
    );
    // A receipt cannot turn an archive without Node into an accepted runtime.
    await create({ cwd: runtime, file: archive, gzip: true }, ['server', 'mac-runtime.json']);
    await save({
      ...receipt,
      archive_sha256: await hash_archive(archive),
      archive_size: (await stat(archive)).size,
    });
    await expect(validate_mac_publication(root, source, 456, 'success', execution)).rejects.toThrow(
      'TypeScript runtime',
    );
  } finally {
    owner.seal();
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
});
