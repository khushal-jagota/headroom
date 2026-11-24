import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { loadConfig, update_repository_configuration } from './config.ts';

test('server paths and secrets follow the settings folder across relocation', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-server-paths-'));
  try {
    const settings = join(root, 'config.yaml');
    await writeFile(settings, 'server_root: state\ndb_path: record.db\n');
    const env = { PLAN_APNS_KEY_ID: 'key', PLAN_APNS_TEAM_ID: 'team', PLAN_APNS_BUNDLE_IDS: 'app' };
    const config = loadConfig(settings, env);
    const state = join(root, 'state');
    expect(config.db_path).toBe(join(state, 'record.db'));
    expect(config.credential_key_path).toBe(join(state, 'credential-secrets/key'));
    expect(config.apns?.key_path).toBe(join(state, 'secrets/apns-key.p8'));
    expect(config.logs_dir).toBe(join(state, 'logs'));
    expect(config.backup_dir).toBe(join(state, 'backups'));
    expect(config.dispatcher_lock_path).toBe(join(state, 'dispatcher.lock'));
    expect(
      update_repository_configuration({ PLAN_UPDATE_REPOSITORY: 'owner/repo' }, state)?.token_file,
    ).toBe(join(state, 'secrets/update-token'));
    expect(loadConfig(settings, {}).apns).toBeNull();
    const override = loadConfig(settings, { PLAN_DB_PATH: root + '/other/../isolated/custom.db' });
    expect(override.logs_dir).toBe(join(root, 'isolated/logs'));
    const legacy = loadConfig(join(root, 'absent.yaml'), { PLAN_DB_PATH: join(root, 'custom.db') });
    expect(legacy.logs_dir).toBe('data/logs');
    expect(legacy.backup_dir).toBe('data/backups');
    expect(legacy.dispatcher_lock_path).toBe('data/dispatcher.lock');
    await writeFile(settings, 'server_root: state\ndb_path: nested/record.db\n');
    expect(() => loadConfig(settings, {})).toThrow('directly inside server_root');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
