import {
  mkdirSync,
  mkdtempSync,
  readlinkSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
  lstatSync,
  closeSync,
  openSync,
} from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { flock } from '../core/locks/flock.ts';
import { setTimeout as pause } from 'node:timers/promises';
import { afterEach, expect, test, vi } from 'vitest';
import { loadConfig, EXTERNAL_RUNTIME_ENVIRONMENT_KEYS } from '../core/config.ts';
import { publish_worker_skills } from '../worker_settings/startup.ts';
import {
  account_skill_homes,
  account_skill_provisioning_conflict,
  provision_account_skills,
} from './account.ts';
import { environment_provision_skills } from '../environments/cli.ts';
const roots: string[] = [];
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'account-skills-'));
  roots.push(root);
  const parent = join(root, 'data'),
    homes = ['hermes', 'codex', 'claude'].map((name) => join(root, name));
  for (const name of ['panels', 'panels-worker', 'panels-worker-personal-task']) {
    mkdirSync(join(parent, 'skills', name), { recursive: true });
    writeFileSync(join(parent, 'skills', name, 'SKILL.md'), name);
  }
  return { root, parent, homes };
}
test('account publication preflights all homes, reconciles only owned links, and preserves foreign entries', async () => {
  const { root, parent, homes } = fixture();
  expect(account_skill_provisioning_conflict(parent, homes)).toBeNull();
  expect(readdirSync(root)).toEqual(['data']);
  expect(provision_account_skills(parent, homes)).toBeNull();
  const link = join(homes[0], 'skills', 'panels'),
    inode = lstatSync(link).ino;
  const hidden = join(homes[0], 'skills', 'panels-worker-personal-task');
  symlinkSync(join(parent, 'skills', 'panels-worker-personal-task'), hidden);
  expect(provision_account_skills(parent, homes)).toBeNull();
  expect(lstatSync(link).ino).toBe(inode);
  expect(readdirSync(join(homes[0], 'skills'))).not.toContain('panels-worker-personal-task');
  const foreign = join(homes[0], 'skills', 'panels-rollover');
  symlinkSync(join(root, 'foreign'), foreign);
  const unused = join(root, 'unused');
  expect(provision_account_skills(parent, [unused, ...homes])).toContain(
    'one instance per OS account',
  );
  expect(readdirSync(root)).not.toContain('unused');
  expect(readlinkSync(foreign)).toBe(join(root, 'foreign'));
  expect(lstatSync(link).ino).toBe(inode);
  const result = await environment_provision_skills(
    { database_parent: parent, hermes_home: homes[0], codex_home: homes[1], claude_home: homes[2] },
    { expand_path: (value) => value, admit_path: (value) => value },
  );
  expect(result).toMatchObject({ exit_code: 0, stdout: '' });
  expect('stderr' in result && result.stderr).toContain('one instance per OS account');
  const legacy = join(root, 'legacy');
  mkdirSync(legacy);
  symlinkSync(join(parent, 'skills'), join(legacy, 'skills'));
  expect(provision_account_skills(parent, [legacy])).toBeNull();
  expect(lstatSync(join(legacy, 'skills')).isDirectory()).toBe(true);
  expect(readlinkSync(join(legacy, 'skills', 'panels'))).toBe(join(parent, 'skills', 'panels'));
  const unreadable = join(root, 'unreadable');
  writeFileSync(unreadable, 'not a directory');
  expect(account_skill_provisioning_conflict(unreadable, homes)).toContain(
    'belongs to unreadable ownership (',
  );
  expect(provision_account_skills(unreadable, homes)).toContain(
    'belongs to an unreadable or conflicting account entry (',
  );
});
test('startup honors provider homes and provisioning choice in test mode, and a conflict warns without aborting', async () => {
  const { root, parent, homes } = fixture();
  for (const [key, value] of Object.entries({
    HOME: root,
    PLAN_HERMES_HOME: homes[0],
    CODEX_HOME: homes[1],
    CLAUDE_CONFIG_DIR: homes[2],
  }))
    vi.stubEnv(key, value);
  expect(account_skill_homes()).toEqual(homes);
  const config = loadConfig(join(root, 'absent.yaml'), {
    PLAN_DB_PATH: join(parent, 'db'),
    PLAN_PROVISION_ACCOUNT_SKILLS: 'true',
  });
  expect(config.provision_account_skills).toBe(true);
  expect(() =>
    loadConfig(join(root, 'absent.yaml'), { PLAN_PROVISION_ACCOUNT_SKILLS: 'bad' }),
  ).toThrow('invalid boolean');
  expect(EXTERNAL_RUNTIME_ENVIRONMENT_KEYS.has('CODEX_HOME')).toBe(true);
  expect(EXTERNAL_RUNTIME_ENVIRONMENT_KEYS.has('CLAUDE_CONFIG_DIR')).toBe(true);
  config.test_mode = true;
  await publish_worker_skills(config);
  expect(readlinkSync(join(homes[1], 'skills', 'panels'))).toBe(join(parent, 'skills', 'panels'));
  rmSync(join(homes[1], 'skills', 'panels'));
  mkdirSync(join(homes[1], 'skills', 'panels'));
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  await expect(publish_worker_skills(config)).resolves.toBeUndefined();
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('one instance per OS account'));
  config.provision_account_skills = false;
  warn.mockClear();
  await publish_worker_skills(config);
  expect(warn).not.toHaveBeenCalled();
  config.provision_account_skills = true;
  await publish_worker_skills(config);
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('one instance per OS account'));
});

test('startup publication keeps the event loop responsive while a provider lock is held', async () => {
  const { root, parent, homes } = fixture();
  for (const [key, value] of Object.entries({
    HOME: root,
    PLAN_HERMES_HOME: homes[0],
    CODEX_HOME: homes[1],
    CLAUDE_CONFIG_DIR: homes[2],
  }))
    vi.stubEnv(key, value);
  const first = [...homes].sort()[0]!;
  mkdirSync(first);
  const fd = openSync(join(first, '.panels-skills.lock'), 'a+', 0o600);
  expect(flock(fd, 'exclusive')).toBe(0);
  const config = loadConfig(join(root, 'absent.yaml'), {
    PLAN_DB_PATH: join(parent, 'db'),
    PLAN_PROVISION_ACCOUNT_SKILLS: 'true',
  });
  config.test_mode = false;
  let settled = false;
  const pending = publish_worker_skills(config).then(() => {
    settled = true;
  });
  try {
    await pause(50);
    expect(settled).toBe(false);
  } finally {
    flock(fd, 'unlock');
    closeSync(fd);
  }
  await pending;
  expect(settled).toBe(true);
  expect(readlinkSync(join(first, 'skills', 'panels'))).toBe(join(parent, 'skills', 'panels'));
});
