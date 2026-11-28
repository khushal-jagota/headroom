import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { agent_home_text, prepare_agent_home } from './agent_home.ts';

let home: string;
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'panels-agent-home-'));
});
afterEach(() => rmSync(home, { recursive: true, force: true }));

test('a new home gets the initial files and folders for its role', async () => {
  await prepare_agent_home(home, 'manager');
  expect(readFileSync(join(home, 'CLAUDE.md'), 'utf8')).toBe(agent_home_text('manager'));
  expect(readFileSync(join(home, 'AGENTS.md'), 'utf8')).toBe(agent_home_text('manager'));
  expect(readdirSync(join(home, 'rules'))).toEqual([]);
  expect(readdirSync(join(home, 'logs'))).toEqual([]);
  const worker = mkdtempSync(join(tmpdir(), 'panels-agent-home-'));
  try {
    await prepare_agent_home(worker, 'worker');
    expect(readdirSync(worker).sort()).toEqual(['AGENTS.md', 'CLAUDE.md', 'logs']);
    expect(readFileSync(join(worker, 'CLAUDE.md'), 'utf8')).toBe(agent_home_text('worker'));
  } finally {
    rmSync(worker, { recursive: true, force: true });
  }
});

// Distinct risk: Panels starts an agent in its home at every start. A rewrite there
// would erase the agent's own router and its other files. No other test covers this.
test.each(['manager', 'worker'] as const)(
  'a %s home keeps its own CLAUDE.md, AGENTS.md and other files at the next start',
  async (role) => {
    writeFileSync(join(home, 'CLAUDE.md'), 'my own router');
    writeFileSync(join(home, 'AGENTS.md'), 'my own router, for Codex');
    writeFileSync(join(home, 'notes.md'), 'notes');
    mkdirSync(join(home, 'rules'));
    writeFileSync(join(home, 'rules', 'distribution.md'), 'a rule');

    await prepare_agent_home(home, role);
    await prepare_agent_home(home, role);

    expect(readFileSync(join(home, 'CLAUDE.md'), 'utf8')).toBe('my own router');
    expect(readFileSync(join(home, 'AGENTS.md'), 'utf8')).toBe('my own router, for Codex');
    expect(readFileSync(join(home, 'notes.md'), 'utf8')).toBe('notes');
    expect(readFileSync(join(home, 'rules', 'distribution.md'), 'utf8')).toBe('a rule');
    expect(readdirSync(join(home, 'logs'))).toEqual([]);
  },
);

test('the Manager file is a short router that names its folders and holds no rules', () => {
  const text = agent_home_text('manager');
  for (const part of [
    '`rules/`',
    '`logs/`',
    '`notes.md`',
    'tell the Chief',
    '`panels page edit`',
    'Keep this file current',
  ])
    expect(text).toContain(part);
  expect(text.split('\n').length).toBeLessThan(40);
});
