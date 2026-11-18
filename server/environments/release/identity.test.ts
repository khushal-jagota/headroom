import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { validate_app_manifest } from './identity.ts';

// On 18 Nov 2026 an old server refused a release that deleted a file the old code listed.
// An update checks a release against what the release says, not against this version's lists.
test('a release that lacks this version’s files and constants still validates', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-identity-'));
  const file = async (name: string, text: string, mode = 0o644) => {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), text, { mode });
  };
  try {
    const inventory = '{"files":[]}\n';
    const contract = {
      version: 2,
      system: 'linux',
      architecture: 'x86_64',
      platforms: [{ distribution: 'debian', version: '14', minimum_glibc: '2.45' }],
      node_major: 24,
      minimum_node_version: '24.1.0',
      cpu_features: ['sse2'],
      external_libraries: ['libc.so.6'],
      external_executables: ['node'],
      inventory_sha256: createHash('sha256').update(inventory).digest('hex'),
      added_by_a_later_version: true,
    };
    await file('runtime/inventory.json', inventory);
    await file('runtime/contract.json', JSON.stringify(contract));
    for (const name of ['bin/panels', 'bin/panels-launcher'])
      await file(name, '#!/bin/sh\n', 0o755);
    await file(
      'manifest.json',
      JSON.stringify({
        format: 'panels-app-v1',
        app_sha: 'a'.repeat(40),
        source_digest: 'b'.repeat(64),
        artifact_digest: 'c'.repeat(64),
        runtime_contract: contract,
      }),
    );
    const validate = () =>
      validate_app_manifest(join(root, 'manifest.json'), {
        require_runtime: true,
        verify_artifact_digest: false,
      });
    expect((await validate()).runtime_contract).toEqual(contract);
    await rm(join(root, 'bin/panels-launcher'));
    await expect(validate()).rejects.toThrow('runtime file is missing: bin/panels-launcher');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
