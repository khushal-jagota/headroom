import { expect, test, vi } from 'vitest';
import { request } from 'node:http';
import { request as secure_request } from 'node:https';
import { send } from './http.ts';
import { run } from './main.ts';
import { CliFailure } from './errors.ts';
import { create_commands } from './commands.ts';
import { parse } from './parser.ts';

// No fixture host can prove refusal before transport receives a bearer secret.
// Mock the existing Node transport boundary and retain the real URL construction.
vi.mock('node:http', async (original) => ({
  ...(await original<typeof import('node:http')>()),
  request: vi.fn(() => {
    throw new Error('unexpected network request');
  }),
}));
vi.mock('node:https', async (original) => ({
  ...(await original<typeof import('node:https')>()),
  request: vi.fn(() => {
    throw new Error('unexpected network request');
  }),
}));

test('api refuses relative paths and shared transport refuses an origin escape before credentials leave', async () => {
  const environment = {
    PLAN_SERVER_URL: 'http://configured.invalid:8767',
    PLAN_LAUNCH_TOKEN: 'synthetic-private-token',
  };
  const reply = await run(['api', 'GET', '@attacker.invalid/x', '--json'], environment);
  expect(reply.exit_code).toBe(1);
  expect(JSON.parse(reply.stderr).error.code).toBe('validation');
  const invocation = {
    ...parse(['api', 'GET', '/api/meta', '--json'], create_commands, environment),
    environment,
    stdin: async () => Buffer.alloc(0),
  };
  await expect(send(invocation, 'GET', '@attacker.invalid/x')).rejects.toBeInstanceOf(CliFailure);
  expect(request).not.toHaveBeenCalled();
  expect(secure_request).not.toHaveBeenCalled();
});
