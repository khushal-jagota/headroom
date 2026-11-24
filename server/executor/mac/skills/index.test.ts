/** Existing publisher host proves the transition from old server links to machine-owned links. */
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readlinkSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { MacSkillsPublisher } from './index.ts';

const skill = (name: string) => ({
  skill_name: name,
  source_text: `---\nname: ${name}\ndescription: ${name} test skill\n---\n\nBody.\n`,
});
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

test('publication links into the home skills folder the driver names', async () => {
  const root = mkdtempSync(join(tmpdir(), 'mac-skills-'));
  roots.push(root);
  const home = join(root, 'provider');
  mkdirSync(join(home, 'agent', 'skills'), { recursive: true });
  const server = join(root, 'server-data');
  for (const name of ['panels', 'panels-worker']) {
    mkdirSync(join(server, 'skills', name), { recursive: true });
    writeFileSync(join(server, 'skills', name, 'SKILL.md'), 'old server copy');
    symlinkSync(join(server, 'skills', name), join(home, 'agent', 'skills', name));
  }
  const publisher = new MacSkillsPublisher((error) => {
    throw error;
  });
  try {
    await publisher.install_native_skill_snapshot(
      {
        required_skill_names: ['panels', 'panels-worker'],
        sources: [skill('panels'), skill('panels-worker')],
      },
      {
        root: join(root, 'state'),
        native_home: home,
        native_skills_directory: 'agent/skills',
        owner_kind: 'ticket',
        legacy_server_skill_parent: server,
      },
    );
  } finally {
    await publisher.stop({ deadline: performance.now() / 1000 + 5 });
  }
  expect(readlinkSync(join(home, 'agent', 'skills', 'panels'))).toContain(
    join(root, 'state', 'skills'),
  );
  expect(readdirSync(join(home, 'agent', 'skills')).sort()).toEqual(['panels', 'panels-worker']);
});
