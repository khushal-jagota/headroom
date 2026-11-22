import { cp, mkdir, mkdtemp, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { relative_manifest, sha256_file } from '../backup/evidence.ts';
import { server_state_manifest, server_state_valid } from './inventory.ts';
import type { ServerStatePaths } from './contracts.ts';

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'panels-state-'));
  roots.push(root);
  const paths: ServerStatePaths = {
    server_root: root,
    database_path: join(root, 'record.sqlite'),
    credential_key_path: join(root, 'credential-secrets/key'),
    apns_key_path: join(root, 'secrets/push.p8'),
    update_token_path: join(root, 'secrets/update-token'),
  };
  async function put(path: string, bytes = path) {
    await mkdir(dirname(join(root, path)), { recursive: true });
    await writeFile(join(root, path), bytes);
  }
  await put('record.sqlite', 'checkpointed database image');
  await put('credential-secrets/key', 'key');
  await put('files/tickets/example/artifacts/evidence.png', 'image');
  await put('skills/example/SKILL.md', 'possibly unmanaged disk-only skill');
  await put('skills/example/references/guide.md', 'support file');
  await put('secrets/push.p8', 'push key');
  await put('secrets/update-token', 'token');
  return { root, paths, put };
}

// Gap: backup proves only its small scope. A move must include secrets and served files,
// exclude machine custody, and detect complete-set changes after relocation.
test('the full inventory covers the five state classes and verifies the same bytes at another root', async () => {
  const f = await fixture();
  for (const excluded of [
    'config.yaml',
    'dispatcher.lock',
    'server-control.sock',
    'updates/attempt.json',
    'metrics.sqlite',
    'backups/old/database.sqlite',
    'executor-uploads/abandoned',
    'worker-settings/settings.json',
    'skills/.publication.lock',
    'skills/example/.SKILL.md.tmp-123-01234567-89ab-cdef-0123-456789abcdef',
    'files/conversations/example/.0123456789abcdef0123456789abcdef',
    'providers/codex/auth.json',
    'files/tickets/example/worktree/node_modules/large',
    'files/tickets/example/.panels-workspace.json',
    'files/tickets/example/.panels-workspace.json.0123456789abcdef0123456789abcdef.tmp',
  ])
    await f.put(excluded);
  await f.put('files/tickets/example/artifacts/back\\slash.txt', 'backslash filename');
  const manifest = await server_state_manifest(f.paths);
  expect(Object.keys(manifest)).toEqual([
    'credential-secrets/key',
    'files/tickets/example/artifacts/back\\slash.txt',
    'files/tickets/example/artifacts/evidence.png',
    'record.sqlite',
    'secrets/push.p8',
    'secrets/update-token',
    'skills/example/SKILL.md',
    'skills/example/references/guide.md',
  ]);
  expect(manifest['record.sqlite']).toBe(await sha256_file(f.paths.database_path));
  const backupSkills = await relative_manifest(join(f.root, 'skills'));
  expect(backupSkills).toMatchObject({
    '.publication.lock': await sha256_file(join(f.root, 'skills/.publication.lock')),
    'example/SKILL.md': manifest['skills/example/SKILL.md'],
    'example/references/guide.md': manifest['skills/example/references/guide.md'],
  });
  const copy = await mkdtemp(join(tmpdir(), 'panels-state-copy-'));
  roots.push(copy);
  await cp(f.root, copy, { recursive: true });
  const moved = {
    server_root: copy,
    database_path: join(copy, 'record.sqlite'),
    credential_key_path: join(copy, 'credential-secrets/key'),
    apns_key_path: join(copy, 'secrets/push.p8'),
    update_token_path: join(copy, 'secrets/update-token'),
  };
  expect(await server_state_valid(moved, manifest)).toBe(true);
  await writeFile(moved.update_token_path, 'changed token');
  expect(await server_state_valid(moved, manifest)).toBe(false);
  await writeFile(moved.update_token_path, 'token');
  await unlink(join(copy, 'files/tickets/example/artifacts/evidence.png'));
  expect(await server_state_valid(moved, manifest)).toBe(false);
  await writeFile(join(copy, 'files/tickets/example/artifacts/evidence.png'), 'image');
  await writeFile(join(copy, 'secrets/additional'), 'new secret');
  expect(await server_state_valid(moved, manifest)).toBe(false);
});

// Gap: backup-v2 ignores links, but a full proof must refuse incomplete inventory.
// SQLite WAL and external configured paths otherwise silently omit required state.
test('a full inventory refuses links, live WAL, and secrets outside its explicit folder', async () => {
  const f = await fixture();
  await symlink(f.paths.update_token_path!, join(f.root, 'skills/example/linked'));
  await expect(server_state_manifest(f.paths)).rejects.toThrow('not a regular file');
  await unlink(join(f.root, 'skills/example/linked'));
  await f.put('record.sqlite-wal', 'committed pages');
  await expect(server_state_manifest(f.paths)).rejects.toThrow('checkpoint');
  await unlink(f.paths.database_path + '-wal');
  await expect(
    server_state_manifest({
      ...f.paths,
      credential_key_path: join(dirname(f.root), 'outside-key'),
    }),
  ).rejects.toThrow('credential key');
  await expect(
    server_state_manifest({
      ...f.paths,
      update_token_path: join(dirname(f.root), 'outside-token'),
    }),
  ).rejects.toThrow('secrets folder');
  await rename_key_directory(f.root);
  await expect(server_state_manifest(f.paths)).rejects.toThrow('not a real directory');
});

async function rename_key_directory(root: string) {
  await mkdir(join(root, 'elsewhere'));
  await rm(join(root, 'credential-secrets'), { recursive: true });
  await symlink(join(root, 'elsewhere'), join(root, 'credential-secrets'));
}
