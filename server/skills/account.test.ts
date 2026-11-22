import {
  mkdirSync,
  mkdtempSync,
  readlinkSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, test } from 'vitest';
import { account_skill_homes, remove_server_account_skill_links } from './account.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

// Machine code refuses an entry it does not own, so the server's old links in a provider account
// folder would stop its first Ticket start. No other test proves the server removes only its own.
test('startup removes only the server links from each provider account skills folder', () => {
  const root = mkdtempSync(join(tmpdir(), 'account-skills-'));
  roots.push(root);
  const parent = join(root, 'data'),
    machine = join(root, 'machine-code/skills');
  for (const source of [join(parent, 'skills'), machine]) {
    mkdirSync(join(source, 'panels'), { recursive: true });
    writeFileSync(join(source, 'panels', 'SKILL.md'), 'panels');
  }
  const environment: NodeJS.ProcessEnv = { HOME: root };
  for (const { manifest } of BUILT_IN_MANIFESTS)
    if (manifest.home)
      environment[manifest.home.environment_name] = join(
        root,
        manifest.home.isolated_directory_name,
      );
  const [first, second, ...rest] = account_skill_homes(environment);
  const folder = (home: typeof first) => join(home!.path, home!.skills_directory);
  for (const home of [first, second, ...rest]) mkdirSync(folder(home), { recursive: true });
  symlinkSync(join(parent, 'skills', 'panels'), join(folder(first), 'panels'));
  symlinkSync(join(parent, 'skills', 'retired'), join(folder(first), 'retired'));
  mkdirSync(join(folder(first), 'own-skill'));
  symlinkSync(join(machine, 'panels'), join(folder(second), 'panels'));

  expect(remove_server_account_skill_links(parent, [first!, second!, ...rest])).toBeNull();
  expect(readdirSync(folder(first))).toEqual(['own-skill']);
  expect(readlinkSync(join(folder(second), 'panels'))).toBe(join(machine, 'panels'));
  // A home that held no server link gets no lock file.
  expect(readdirSync(second!.path)).toEqual([second!.skills_directory.split('/')[0]]);
});
