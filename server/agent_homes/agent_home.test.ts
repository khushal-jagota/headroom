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

// Distinct risk: an existing Manager home keeps notes.md and its rules when Panels
// writes the new router file over it. No other test covers prepare_agent_home.
test('a Manager home gets the router and rules/ and keeps what it already holds', async () => {
  writeFileSync(join(home, 'notes.md'), 'manager notes');
  writeFileSync(join(home, 'CLAUDE.md'), 'old file');
  mkdirSync(join(home, 'rules'));
  writeFileSync(join(home, 'rules', 'distribution.md'), 'a rule');

  await prepare_agent_home(home, 'manager');
  await prepare_agent_home(home, 'manager');

  expect(readFileSync(join(home, 'notes.md'), 'utf8')).toBe('manager notes');
  expect(readFileSync(join(home, 'rules', 'distribution.md'), 'utf8')).toBe('a rule');
  expect(readdirSync(join(home, 'logs'))).toEqual([]);
  const text = agent_home_text('manager');
  expect(readFileSync(join(home, 'CLAUDE.md'), 'utf8')).toBe(text);
  expect(readFileSync(join(home, 'AGENTS.md'), 'utf8')).toBe(text);
});

test('a new Manager home gets an empty rules/ folder', async () => {
  await prepare_agent_home(home, 'manager');
  expect(readdirSync(join(home, 'rules'))).toEqual([]);
});

test('the Manager file is a short router that names its folders and holds no rules', () => {
  const text = agent_home_text('manager');
  for (const part of [
    '`rules/`',
    '`logs/`',
    '`notes.md`',
    'tell the Chief',
    'Do not copy them here',
  ])
    expect(text).toContain(part);
  expect(text.split('\n').length).toBeLessThan(40);
});

test('the Worker file has no rules/ folder and no Manager text', async () => {
  await prepare_agent_home(home, 'worker');
  expect(readdirSync(home).sort()).toEqual(['AGENTS.md', 'CLAUDE.md', 'logs']);
  expect(agent_home_text('worker')).not.toContain('rules/');
  expect(agent_home_text('worker')).toContain('You are the Worker for one Ticket.');
});
