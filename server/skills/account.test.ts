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
import { PROVISION_SKILLS_HOME_OPTIONS } from '../environments/command_contracts.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
const roots: string[] = [];
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
/** One home per built-in driver home, under root, with that driver's skills folder. */
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'account-skills-'));
  roots.push(root);
  const parent = join(root, 'data'),
    homes = PROVISION_SKILLS_HOME_OPTIONS.map(({ destination, home }) => ({
      destination,
      environment_name: home.environment_name,
      path: join(root, home.isolated_directory_name),
      skills_directory: home.skills_directory ?? 'skills',
    }));
  for (const name of ['panels', 'panels-worker', 'panels-worker-personal-task']) {
    mkdirSync(join(parent, 'skills', name), { recursive: true });
    writeFileSync(join(parent, 'skills', name, 'SKILL.md'), name);
  }
  return { root, parent, homes };
}
const skills_of = (home: { path: string; skills_directory: string }) =>
  join(home.path, home.skills_directory);
function stub_homes(root: string, homes: ReturnType<typeof fixture>['homes']) {
  vi.stubEnv('HOME', root);
  for (const home of homes) vi.stubEnv(home.environment_name, home.path);
}
test('account publication preflights all homes, reconciles only owned links, and preserves foreign entries', async () => {
  const { root, parent, homes } = fixture();
  expect(account_skill_provisioning_conflict(parent, homes)).toBeNull();
  expect(readdirSync(root)).toEqual(['data']);
  expect(provision_account_skills(parent, homes)).toBeNull();
  const link = join(skills_of(homes[0]!), 'panels'),
    inode = lstatSync(link).ino;
  const hidden = join(skills_of(homes[0]!), 'panels-worker-personal-task');
  symlinkSync(join(parent, 'skills', 'panels-worker-personal-task'), hidden);
  expect(provision_account_skills(parent, homes)).toBeNull();
  expect(lstatSync(link).ino).toBe(inode);
  expect(readdirSync(skills_of(homes[0]!))).not.toContain('panels-worker-personal-task');
  const foreign = join(skills_of(homes[0]!), 'panels-rollover');
  symlinkSync(join(root, 'foreign'), foreign);
  const unused = join(root, 'unused');
  expect(provision_account_skills(parent, [unused, ...homes])).toContain(
    'one instance per OS account',
  );
  expect(readdirSync(root)).not.toContain('unused');
  expect(readlinkSync(foreign)).toBe(join(root, 'foreign'));
  expect(lstatSync(link).ino).toBe(inode);
  const result = await environment_provision_skills(
    {
      database_parent: parent,
      ...Object.fromEntries(homes.map(({ destination, path }) => [destination, path])),
    },
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
  stub_homes(root, homes);
  expect(account_skill_homes()).toEqual(
    homes.map(({ path, skills_directory }) => ({ path, skills_directory })),
  );
  const config = loadConfig(join(root, 'absent.yaml'), {
    PLAN_DB_PATH: join(parent, 'db'),
    PLAN_PROVISION_ACCOUNT_SKILLS: 'true',
  });
  config.workspace_root = join(root, 'workspace');
  mkdirSync(config.workspace_root);
  expect(config.provision_account_skills).toBe(true);
  expect(() =>
    loadConfig(join(root, 'absent.yaml'), { PLAN_PROVISION_ACCOUNT_SKILLS: 'bad' }),
  ).toThrow('invalid boolean');
  for (const home of homes)
    expect(EXTERNAL_RUNTIME_ENVIRONMENT_KEYS.has(home.environment_name)).toBe(true);
  config.test_mode = true;
  await publish_worker_skills(config);
  for (const home of homes)
    expect(readlinkSync(join(skills_of(home), 'panels'))).toBe(join(parent, 'skills', 'panels'));
  rmSync(join(skills_of(homes[0]!), 'panels'));
  mkdirSync(join(skills_of(homes[0]!), 'panels'));
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
  stub_homes(root, homes);
  const first = [...homes].sort((a, b) => (a.path < b.path ? -1 : 1))[0]!;
  mkdirSync(first.path);
  const fd = openSync(join(first.path, '.panels-skills.lock'), 'a+', 0o600);
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
  expect(readlinkSync(join(skills_of(first), 'panels'))).toBe(join(parent, 'skills', 'panels'));
});

// Gap: no other test proves a manifest's nested skills_directory, rather than 'skills', receives
// the links through folders Panels makes, and that a linked intermediate folder is never followed.
test('a driver home that names a nested skills folder gets its skills linked there', () => {
  const { root, parent } = fixture();
  const [driver] = BUILT_IN_MANIFESTS;
  const home = { ...driver!.manifest.home!, skills_directory: 'agent/skills' };
  const homes = account_skill_homes(
    { HOME: root, [home.environment_name]: join(root, 'scripted') },
    [{ manifest: { ...driver!.manifest, home } }],
  );
  expect(homes).toEqual([{ path: join(root, 'scripted'), skills_directory: 'agent/skills' }]);
  expect(provision_account_skills(parent, homes)).toBeNull();
  expect(readlinkSync(join(root, 'scripted', 'agent', 'skills', 'panels'))).toBe(
    join(parent, 'skills', 'panels'),
  );
  expect(readdirSync(join(root, 'scripted')).sort()).toEqual(['.panels-skills.lock', 'agent']);
  const linked = { path: join(root, 'linked'), skills_directory: 'agent/skills' };
  mkdirSync(join(root, 'elsewhere'));
  mkdirSync(linked.path);
  symlinkSync(join(root, 'elsewhere'), join(linked.path, 'agent'));
  expect(provision_account_skills(parent, [linked])).toContain('one instance per OS account');
  expect(readdirSync(join(root, 'elsewhere'))).toEqual([]);
});
