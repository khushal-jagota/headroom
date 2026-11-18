import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { SignInReturnUrlRefused, SignInUnavailable } from '../../core/sign_in.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { UnknownSignInSession } from '../../core/sign_in.ts';
import { AntigravitySignIn } from './sign_in.ts';

/**
 * A stand-in for `agy` 1.5.10 on its default app data directory, where the CLI keeps its login.
 * `acp --login` prints its Google link as the real one does, reads the return address from stdin,
 * and saves the login for the right code. `acp` answers ACP `initialize` with logout support, and
 * removes the login on `logout`.
 */
const FAKE_AGY = `#!/usr/bin/env node
const { mkdirSync, rmSync, writeFileSync } = require('node:fs');
const { join } = require('node:path');
const { createInterface } = require('node:readline');
if (process.argv[2] === '--version') { console.log('1.5.10'); process.exit(0); }
const home = process.argv[process.argv.indexOf('--gemini-dir') + 1];
// Panels leaves agy on its default app data directory, so it acts on the CLI login.
if (process.argv.includes('--app-data-dir')) process.exit(2);
const state = join(home, 'antigravity-cli');
const lines = createInterface({ input: process.stdin });
if (process.argv.includes('--login')) {
  writeFileSync(join(home, 'login.pid'), String(process.pid));
  const link = 'https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=fake' +
    '&redirect_uri=' + encodeURIComponent('https://antigravity.google/oauth-callback') +
    '&response_type=code&scope=openid&state=fake-state';
  process.stdout.write('Open this link in a browser to log in with Google:\\n\\n  ' + link +
    '\\n\\nThen paste the authorization code, or the full URL of the page you land on, and press Enter:\\n');
  lines.once('line', (line) => {
    const url = new URL(line);
    if (url.searchParams.get('code') !== 'good-code') {
      console.log("couldn't log in: token exchange failed");
      process.exit(1);
    }
    mkdirSync(state, { recursive: true });
    writeFileSync(join(state, 'antigravity-oauth-token'), '{}');
    process.exit(0);
  });
} else {
  const send = (message) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...message }) + '\\n');
  lines.on('line', (line) => {
    const { id, method } = JSON.parse(line);
    if (method === 'initialize')
      send({ id, result: { protocolVersion: 1, agentCapabilities: { auth: { logout: {} } } } });
    else if (method === 'logout') {
      rmSync(join(state, 'antigravity-oauth-token'), { force: true });
      send({ id, result: {} });
    }
  });
}
`;
const RETURN = 'https://antigravity.google/oauth-callback';
let home: string;
let agy: string;
let sign_in: AntigravitySignIn;
const children = new ChildProcesses();
const login = () => join(home, 'antigravity-cli', 'antigravity-oauth-token');
const login_is_running = () => {
  try {
    process.kill(Number(readFileSync(join(home, 'login.pid'), 'utf8')), 0);
    return true;
  } catch {
    return false;
  }
};
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'agy-sign-in-'));
  agy = join(home, 'agy');
  writeFileSync(agy, FAKE_AGY);
  chmodSync(agy, 0o755);
  sign_in = new AntigravitySignIn(
    { home, worker_process_root: home, panels_server_url: 'http://127.0.0.1:9' },
    {
      executable: () => agy,
      child_processes: children,
      one_shot_children: new OneShotChildren(children),
      verify: async () => existsSync(login()),
    },
  );
});
afterEach(async () => {
  await sign_in.shutdown();
  rmSync(home, { recursive: true, force: true });
});

// Risk: the remote-device path — agy's printed link, the paste check and the stdin handoff —
// breaks, or a paste from another sign-in reaches agy and ends the sign-in in force.
test('a pasted return address is checked, handed to agy, and confirms the sign-in', async () => {
  const started = await sign_in.start();
  expect(started.state).toBe('awaiting_return_url');
  expect(started.user_code).toBeNull();
  const link = new URL(started.verification_url!);
  expect(link.origin + link.pathname).toBe('https://accounts.google.com/o/oauth2/auth');
  expect(link.searchParams.get('redirect_uri')).toBe(RETURN);

  const refused = sign_in.submit_return_url(
    started.session_id,
    `${RETURN}?code=good-code&state=another-sign-in`,
  );
  await expect(refused).rejects.toBeInstanceOf(SignInReturnUrlRefused);
  expect(sign_in.read()?.state).toBe('awaiting_return_url');
  expect(login_is_running()).toBe(true);

  const confirmed = await sign_in.submit_return_url(
    started.session_id,
    `${RETURN}?state=fake-state&code=good-code&scope=openid`,
  );
  expect(confirmed).toMatchObject({ state: 'confirmed', verification_url: null });
  expect(existsSync(login())).toBe(true);
  expect(login_is_running()).toBe(false);
});

// Risk: a cancelled sign-in leaves agy waiting on stdin, or another session id cancels it.
test('cancel stops agy and ends the sign-in', async () => {
  const started = await sign_in.start();
  await expect(sign_in.cancel('another-session')).rejects.toBeInstanceOf(UnknownSignInSession);
  expect(login_is_running()).toBe(true);
  expect(await sign_in.cancel(started.session_id)).toMatchObject({ state: 'ended' });
  expect(login_is_running()).toBe(false);
});

// Risk: sign-out never reaches agy, or reaches an agy on another state directory, so the CLI
// login stays saved; or the card keeps showing the confirmed sign-in it removed; or a failed
// logout escapes as an unmapped error rather than the route's 503.
test('sign-out removes the CLI login through ACP logout to a short-lived agy', async () => {
  const started = await sign_in.start();
  await sign_in.submit_return_url(started.session_id, `${RETURN}?state=fake-state&code=good-code`);
  expect(sign_in.read()?.state).toBe('confirmed');
  await sign_in.sign_out();
  expect(existsSync(login())).toBe(false);
  expect(sign_in.read()).toBeNull();

  mkdirSync(join(home, 'antigravity-cli'), { recursive: true });
  writeFileSync(login(), '{}');
  writeFileSync(
    agy,
    FAKE_AGY.replace('agentCapabilities: { auth: { logout: {} } }', 'agentCapabilities: {}'),
  );
  await expect(sign_in.sign_out()).rejects.toBeInstanceOf(SignInUnavailable);
  expect(existsSync(login())).toBe(true);
});
