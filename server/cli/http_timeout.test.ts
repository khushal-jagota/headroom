import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { expect, test } from 'vitest';
import { create_commands } from './commands.ts';
import { CliFailure } from './errors.ts';
import { send } from './http.ts';
import { parse } from './parser.ts';

async function with_server(
  handler: (req: IncomingMessage, res: ServerResponse) => void,
  work: (url: string) => Promise<void>,
): Promise<void> {
  const server: Server = createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (address === null || typeof address === 'string') throw new Error('missing server address');
  try {
    await work(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

function invocation(args: string[], url: string) {
  const environment = { PLAN_SERVER_URL: url, PLAN_ACTOR: '' };
  return {
    ...parse(args, create_commands, environment),
    environment,
    stdin: async () => Buffer.alloc(0),
  };
}

// Risk: a slow server response past the read timeout previously reported an outage
// ("The Panels server is unavailable."), hiding that the request may still be running.
test('CLI reports a response read timeout distinctly from an unavailable server', async () => {
  const limits = { connect_ms: 1000, write_ms: 1000, read_ms: 30 };
  const sockets = new Set<import('node:net').Socket>();
  await with_server(
    (req, res) => {
      sockets.add(req.socket);
      req.socket.once('close', () => sockets.delete(req.socket));
      req.resume();
      req.on('end', () => {
        setTimeout(() => {
          if (!res.writableEnded) res.end('{"ok":true}');
        }, 200);
      });
    },
    async (url) => {
      let human_failure: unknown;
      try {
        await send(invocation(['ticket', 'restart-worker', 't_slow'], url), 'POST', '/slow', {
          json_body: {},
          transport_limits: limits,
        });
      } catch (error) {
        human_failure = error;
      }
      expect(human_failure).toBeInstanceOf(CliFailure);
      const human = (human_failure as CliFailure).result;
      expect(human.exit_code).toBe(2);
      expect(human.stderr).toContain('timed out');
      expect(human.stderr).toContain('may still be running');
      expect(human.stderr).not.toContain('unavailable');
      expect(human.stderr).not.toContain('Use:');

      let json_failure: unknown;
      try {
        await send(
          invocation(['ticket', 'restart-worker', 't_slow', '--json'], url),
          'POST',
          '/slow',
          { json_body: {}, transport_limits: limits },
        );
      } catch (error) {
        json_failure = error;
      }
      expect(json_failure).toBeInstanceOf(CliFailure);
      const machine = (json_failure as CliFailure).result;
      expect(machine.exit_code).toBe(2);
      const payload = JSON.parse(machine.stderr);
      expect(payload.error.code).toBe('connection');
      expect(payload.error.message).toContain('timed out');
      expect(payload.error.message).toContain('may still be running');
      for (const socket of sockets) socket.destroy();
    },
  );

  // Control: an unreachable port still reports the server as unavailable.
  const closed = createServer();
  await new Promise<void>((resolve) => closed.listen(0, '127.0.0.1', resolve));
  const address = closed.address() as import('node:net').AddressInfo;
  await new Promise<void>((resolve) => closed.close(() => resolve()));
  let refused_failure: unknown;
  try {
    await send(
      invocation(['ticket', 'restart-worker', 't_down'], `http://127.0.0.1:${address.port}`),
      'POST',
      '/down',
      { json_body: {}, transport_limits: limits },
    );
  } catch (error) {
    refused_failure = error;
  }
  expect(refused_failure).toBeInstanceOf(CliFailure);
  const refused = (refused_failure as CliFailure).result;
  expect(refused.exit_code).toBe(2);
  expect(refused.stderr).toContain('The Panels server is unavailable.');
  expect(refused.stderr).toContain('No panels call can work until the server is available.');
});
