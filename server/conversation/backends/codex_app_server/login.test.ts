/** Risks: false saved-login certainty and duplicate rotating credentials. No provider starts. */
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { child_environment } from './environment.ts';
import { CodexLoginIsDuplicated } from './errors.ts';
import {
  codex_home,
  logins_hold_the_same_credential,
  read_stored_codex_login,
  RememberedCodexLoginRejection,
} from './login.ts';

const roots: string[] = [];
function root(): string {
  const path = mkdtempSync(join(tmpdir(), 'codex-login-'));
  roots.push(path);
  return path;
}
function document(home: string, body: unknown): void {
  mkdirSync(home, { recursive: true });
  writeFileSync(join(home, 'auth.json'), JSON.stringify(body));
}
function token(body: string): string {
  return `header.${Buffer.from(body).toString('base64url')}.signature`;
}
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  for (const path of roots.splice(0)) rmSync(path, { recursive: true, force: true });
});

test('saved-login reads preserve missing, unreadable, expiry, and refreshed generation facts', () => {
  const home = root();
  expect(read_stored_codex_login(home)).toMatchObject({ present: false, readable: false });
  writeFileSync(join(home, 'auth.json'), '{broken');
  expect(read_stored_codex_login(home)).toMatchObject({ present: true, readable: false });
  document(home, {
    last_refresh: ' refreshed ',
    tokens: {
      account_id: ' account ',
      id_token: token('{"email":" owner@example.invalid "}'),
      access_token: token('{"exp":1700000000}'),
    },
  });
  const login = read_stored_codex_login(home);
  expect(login).toMatchObject({
    present: true,
    readable: true,
    account_label: 'owner@example.invalid',
    generation: { account_id: 'account', last_refresh: 'refreshed' },
    access_token_expires_at: new Date('2023-11-14T22:13:20.000Z'),
  });
  expect(login.access_token_has_expired({ now: new Date('2023-11-14T22:13:20.000Z') })).toBe(true);
  expect(login.access_token_has_expired({ now: new Date('2023-11-14T22:13:19.999Z') })).toBe(false);
  document(home, { tokens: { access_token: token('{"exp":1700000000.0}') } });
  expect(read_stored_codex_login(home).access_token_expires_at).toBeNull();
  document(home, { tokens: { access_token: token('{"exp":true}') } });
  expect(read_stored_codex_login(home).access_token_expires_at).toEqual(new Date(1000));
  writeFileSync(join(home, 'auth.json'), Buffer.from([0x7b, 0x22, 0xff, 0x22, 0x3a, 0x31, 0x7d]));
  expect(read_stored_codex_login(home).readable).toBe(false);
});

test('a remembered rejection applies only to its generation and survives unrelated success', () => {
  const memory = new RememberedCodexLoginRejection();
  const original = { account_id: 'account', last_refresh: 'old' };
  const refreshed = { ...original, last_refresh: 'new' };
  memory.remember(original);
  memory.remember(null);
  expect(memory.applies_to({ ...original })).toBe(true);
  expect(memory.applies_to(refreshed)).toBe(false);
  memory.forget(refreshed);
  expect(memory.applies_to(original)).toBe(true);
  memory.forget({ ...original });
  expect(memory.applies_to(original)).toBe(false);
});

test('API-key overlap and unreadable credentials preserve duplicate-check certainty', () => {
  const directory = root();
  const ambient = join(directory, 'ambient');
  const alternate = join(directory, 'alternate');
  vi.stubEnv('HOME', directory);
  vi.stubEnv('CODEX_HOME', ambient);
  document(ambient, { tokens: { refresh_token: 'synthetic-secret', account_id: 'one' } });
  document(alternate, { OPENAI_API_KEY: 'synthetic-secret' });
  expect(logins_hold_the_same_credential(ambient, alternate)).toBe(true);
  expect(() => child_environment({ overrides: [['CODEX_HOME', alternate]] })).toThrow(
    CodexLoginIsDuplicated,
  );
  document(alternate, {});
  expect(logins_hold_the_same_credential(ambient, alternate)).toBe(false);
  writeFileSync(join(alternate, 'auth.json'), 'unreadable');
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  expect(child_environment({ overrides: [['CODEX_HOME', alternate]] }).CODEX_HOME).toBe(alternate);
  expect(warn).toHaveBeenCalledOnce();
  const loop = join(directory, 'loop');
  symlinkSync(loop, loop);
  expect(child_environment({ overrides: [['CODEX_HOME', loop]] }).CODEX_HOME).toBe(loop);
  expect(warn).toHaveBeenCalledTimes(2);
  expect(codex_home({ HOME: '/not-the-process-home' })).toBe(join(directory, '.codex'));
});
