import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { AntigravityModelCatalog } from './catalog.ts';

/**
 * A stand-in for `agy`: `--version`, then ACP over stdio as agy 1.5.10 answers it. It keeps the
 * session's files where agy does, and is signed out when its home holds `signed_out`.
 */
const FAKE_AGY = `#!/usr/bin/env node
const { mkdirSync, writeFileSync, existsSync, readFileSync } = require('node:fs');
const { join } = require('node:path');
const { createInterface } = require('node:readline');
if (process.argv[2] === '--version') { console.log('1.5.10'); process.exit(0); }
const home = process.argv[process.argv.indexOf('--gemini-dir') + 1];
const state = join(home, process.argv[process.argv.indexOf('--app-data-dir') + 1]);
const recorded = JSON.parse(readFileSync(join(home, 'session_new.json'), 'utf8'));
const levels = (model) => model === 'gemini-4-hydrogen' ? ['low', 'medium', 'high', 'xhigh']
  : model === 'gpt-oss-120b-medium' ? null : ['low', 'medium', 'high'];
const options = (model) => recorded.configOptions.flatMap((option) =>
  option.category !== 'thought_level' ? [{ ...option, currentValue: option.category === 'model' ? model : option.currentValue }]
  : levels(model) === null ? [] : [{ ...option, options: levels(model).map((value) => ({ value, name: value })) }]);
const send = (message) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...message }) + '\\n');
createInterface({ input: process.stdin }).on('line', (line) => {
  const { id, method, params } = JSON.parse(line);
  if (method === 'initialize')
    send({ id, result: { protocolVersion: 1, agentCapabilities: {}, agentInfo: { name: 'Antigravity ACP Server', version: '1.5.10' } } });
  else if (method === 'session/new') {
    if (existsSync(join(home, 'signed_out')))
      return send({ id, error: { code: -32000, message: 'Authentication required' } });
    writeFileSync(join(home, 'session_cwd'), params.cwd);
    mkdirSync(join(state, 'conversations'), { recursive: true });
    mkdirSync(join(state, 'brain', recorded.sessionId), { recursive: true });
    writeFileSync(join(state, 'conversations', recorded.sessionId + '.db'), '');
    send({ id, result: recorded });
  } else if (method === 'session/set_config_option')
    send({ id, result: { configOptions: options(params.value) } });
});
`;
let home: string;
let agy: string;
const children = new ChildProcesses();
const catalog = () =>
  new AntigravityModelCatalog(
    { home, worker_process_root: home, panels_server_url: 'http://127.0.0.1:9' },
    { child_processes: children, one_shot_children: new OneShotChildren(children) },
  );
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'agy-catalog-'));
  agy = join(home, 'agy');
  writeFileSync(agy, FAKE_AGY);
  chmodSync(agy, 0o755);
  writeFileSync(
    join(home, 'session_new.json'),
    readFileSync(new URL('./testing/agy_1.5.10_session_new.json', import.meta.url)),
  );
});
afterEach(() => rmSync(home, { recursive: true, force: true }));

// Risk: the only path from a live agy to the card — its process, stdio and cleanup — breaks or
// leaves a throwaway session and directory behind, or a signed-out agy reads as a failure.
test('the catalog reads each model from a throwaway session it then removes', async () => {
  const read = await catalog().read(agy);
  if (!read.signed_in) throw new Error('the fake agy is signed in');
  expect([...read.after_selecting.keys()]).not.toContain('gemini-4-argon');
  const hydrogen = read.after_selecting
    .get('gemini-4-hydrogen')!
    .find((option) => option.category === 'thought_level');
  expect(hydrogen?.type === 'select' && hydrogen.options).toHaveLength(4);
  const session = 'd054a0e0-e154-41e0-8aa7-38cef63181da';
  expect(existsSync(join(home, 'antigravity-acp', 'conversations', `${session}.db`))).toBe(false);
  expect(existsSync(join(home, 'antigravity-acp', 'brain', session))).toBe(false);
  expect(existsSync(readFileSync(join(home, 'session_cwd'), 'utf8'))).toBe(false);

  writeFileSync(join(home, 'signed_out'), '');
  expect(await catalog().read(agy)).toEqual({ signed_in: false });
});
