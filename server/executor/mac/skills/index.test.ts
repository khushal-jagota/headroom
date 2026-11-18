/** A provider that keeps its skills in another, nested folder of its home gets them there. */
import { mkdirSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
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
  const publisher = new MacSkillsPublisher((error) => {
    throw error;
  });
  try {
    await publisher.install_native_skill_snapshot(
      {
        required_skill_names: ['panels', 'panels-worker'],
        sources: [skill('panels'), skill('panels-worker')],
      },
      { root: join(root, 'state'), native_home: home, native_skills_directory: 'agent/skills' },
    );
  } finally {
    await publisher.stop({ deadline: performance.now() / 1000 + 5 });
  }
  expect(readdirSync(join(home, 'agent', 'skills')).sort()).toEqual(['panels', 'panels-worker']);
});
