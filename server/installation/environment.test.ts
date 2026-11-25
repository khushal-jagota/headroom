import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { machine_service_environment } from './environment.ts';
import type { HomeInstallHost, HomeInstallRequest } from './contracts.ts';

/** Only the fields the environment reads. */
const request = (isolated_providers: boolean) =>
  ({ root: join(tmpdir(), 'panels-root'), port: 1, isolated_providers }) as HomeInstallRequest;
const host = (system: 'linux' | 'darwin', environment: NodeJS.ProcessEnv = {}) =>
  ({
    system,
    account_home: mkdtempSync(join(tmpdir(), 'account-')),
    environment,
  }) as HomeInstallHost;

test('a macOS home install leaves CLAUDE_CONFIG_DIR unset so the saved Keychain login stays visible', async () => {
  const mac = await machine_service_environment(request(false), host('darwin'));
  expect(mac).not.toHaveProperty('CLAUDE_CONFIG_DIR');
  expect(mac).toHaveProperty('CODEX_HOME');
});

test('CLAUDE_CONFIG_DIR is still set on Linux, when the owner set it, and for isolated providers', async () => {
  expect(await machine_service_environment(request(false), host('linux'))).toHaveProperty(
    'CLAUDE_CONFIG_DIR',
  );
  expect(
    (
      await machine_service_environment(
        request(false),
        host('darwin', { CLAUDE_CONFIG_DIR: '/owner/claude' }),
      )
    ).CLAUDE_CONFIG_DIR,
  ).toBe('/owner/claude');
  expect(await machine_service_environment(request(true), host('darwin'))).toHaveProperty(
    'CLAUDE_CONFIG_DIR',
  );
});
