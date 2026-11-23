import { createHash } from 'node:crypto';
import { link, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { digest_app_artifact, validate_app_manifest } from './identity.ts';
import { create_app_archive } from './archive.ts';
import { ArchiveMembers } from './logic/archive_members.ts';
import { gunzipSync } from 'node:zlib';

async function release_fixture(root: string) {
  const file = async (name: string, text: string, mode = 0o644) => {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), text, { mode });
  };
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
  for (const name of ['bin/panels', 'bin/panels-launcher']) await file(name, '#!/bin/sh\n', 0o755);
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
  return { file, contract };
}

// On 18 Nov 2026 an old server refused a release that deleted a file the old code listed.
// An update checks a release against what the release says, not against this version's lists.
test('a release that lacks this version’s files and constants still validates', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-identity-'));
  try {
    const { contract } = await release_fixture(root);
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

// Pinned provider installs can share an inode. Historical machine readers reject TAR Link entries.
test('app archives write both shared-inode paths as regular files with complete bytes', async () => {
  const parent = await mkdtemp(join(tmpdir(), 'panels-archive-links-'));
  const root = join(parent, 'app'),
    archive = join(parent, 'app.tar.gz');
  try {
    const { file } = await release_fixture(root);
    const bytes = '#!/bin/sh\nprintf provider\n';
    await file('agent_backends/claude', bytes, 0o755);
    await link(join(root, 'agent_backends/claude'), join(root, 'agent_backends/claude-copy'));
    const manifest = JSON.parse(await readFile(join(root, 'manifest.json'), 'utf8'));
    manifest.artifact_digest = await digest_app_artifact(root);
    await file('manifest.json', JSON.stringify(manifest));
    await create_app_archive(root, archive);
    const bodies = new Map<string, Buffer[]>();
    const members = new ArchiveMembers('extraction', (entry, member) => {
      if (member.path.startsWith('agent_backends/claude')) {
        bodies.set(member.path, []);
        entry.on('data', (chunk: Buffer) => bodies.get(member.path)!.push(chunk));
      }
    });
    members.feed(gunzipSync(await readFile(archive)));
    members.finish();
    expect(
      members.members
        .filter((member) => member.path.startsWith('agent_backends/claude'))
        .map(({ type }) => type),
    ).toEqual(['File', 'File']);
    expect([...bodies.values()].map((chunks) => Buffer.concat(chunks).toString())).toEqual([
      bytes,
      bytes,
    ]);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});
