/** Scripted old and new servers prove where the move stops, what it removes, and how it switches. */
import { createHash } from 'node:crypto';
import { IncomingMessage } from 'node:http';
import { Socket } from 'node:net';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { mkdir, readdir, rename, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import type {
  ServerMoveInstallRequest,
  ServerMoveRemoveRequest,
} from '../../../server_move/contracts.ts';
import type { MachineServerRecord } from '../../../server_link/contracts.ts';
import { FilesService } from '../files.ts';
import { atomic_record } from '../runtime/records.ts';
import { CONNECTORS } from '../transport/http.ts';
import { HttpSessions } from '../transport/sessions.ts';
import type { HttpRequest, ReplyReader } from '../transport/contracts.ts';
import { MacServerMove } from './runner.ts';

const OLD = 'https://old.example';
const MOVE = 'move-1';
const sha = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
interface Answer {
  readonly status?: number;
  readonly body: Buffer | string | object;
  readonly headers?: Record<string, string>;
}
type Route = (body: unknown) => Answer;
const roots: string[] = [],
  workers: FilesService[] = [],
  runners: MacServerMove[] = [];
afterEach(async () => {
  for (const runner of runners.splice(0))
    await runner.stop({ deadline: performance.now() / 1000 + 5 });
  for (const files of workers.splice(0)) {
    await files.stop({ deadline: performance.now() / 1000 + 5 });
    await files.settled();
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function fixture(options: { package_bytes?: Buffer; commit?: boolean } = {}) {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'server-move-')));
  roots.push(home);
  const root = join(home, 'executor');
  const link = join(home, 'new-server.sock');
  const archive = Buffer.from('sealed server package');
  const files_before = { 'state/a.json': '{"a":1}', 'state/gone.json': 'removed later' };
  const files_after = { 'state/a.json': '{"a":2}', 'planner.db': 'database copy' };
  const listed = (entries: Record<string, string>) =>
    Object.fromEntries(Object.entries(entries).map(([path, text]) => [path, sha(text)]));
  let paused = false,
    move_number = 4;
  const reports: unknown[] = [],
    requests: string[] = [],
    identity_on_old: boolean[] = [],
    installs: (ServerMoveInstallRequest | ServerMoveRemoveRequest)[] = [],
    saved: string[] = [];
  const instruction = {
    move_id: MOVE,
    phase: 'copying',
    server_url: 'https://new.example',
    port: 8800,
    server_id: 'server-1',
    move_number,
    package: {
      platform: 'linux',
      revision: 'a'.repeat(40),
      archive_sha256: sha(archive),
      archive_size: archive.length,
    },
    trusted_ingress: null,
  };
  const state_file = (path: string): Answer => {
    const text = (paused ? files_after : files_before) as Record<string, string | undefined>;
    const bytes = text[path];
    if (bytes === undefined) return { status: 404, body: { error: { code: 'missing' } } };
    return { body: bytes, headers: { 'x-panels-file-sha256': sha(bytes) } };
  };
  const routes: Record<string, Route> = {
    [`${OLD} GET /api/executor/server-move`]: () => ({ body: instruction }),
    [`${OLD} GET /api/executor/server-move/${MOVE}/package`]: () => ({
      body: options.package_bytes ?? archive,
    }),
    [`${OLD} GET /api/executor/server-move/${MOVE}/files`]: () => ({
      body: { complete: false, manifest: listed(files_before) },
    }),
    [`${OLD} POST /api/executor/server-move/${MOVE}/pause`]: () => {
      paused = true;
      return { body: { complete: true, manifest: listed(files_after) } };
    },
    [`${OLD} POST /api/executor/server-move/${MOVE}/report`]: (body) => {
      reports.push(body);
      return {
        body: {
          commit: (body as { state: string }).state === 'checked' && options.commit !== false,
        },
      };
    },
    [`${OLD} POST /api/executor/server-move/${MOVE}/switched`]: () => ({ body: {} }),
    [`${link} POST /api/executor/server-move/held/probe`]: () => ({
      body: { job_id: 'job-1', nonce: 'nonce-1' },
    }),
    [`${link} POST /api/executor/server-move/held/probe/job-1`]: (body) => {
      expect(body).toEqual({ nonce: 'nonce-1' });
      return { body: { answered: true } };
    },
    [`${link} GET /api/server-link`]: () => ({
      body: {
        server_id: 'server-1',
        move_number,
        server_url: 'https://new.example',
        state: 'active',
      },
    }),
    [`${link} POST /api/executor/server-move/held/commit`]: () => {
      move_number = 5;
      return { body: { server_id: 'server-1', move_number } };
    },
  };
  class ScriptedSession extends HttpSessions {
    private readonly target: string;
    private readonly checks_identity: boolean;
    constructor(address: string, checks_identity: boolean) {
      super(address, CONNECTORS);
      this.target = address;
      this.checks_identity = checks_identity;
    }
    override async request<T>(
      input: HttpRequest,
      _headers: Readonly<Record<string, string>>,
      consume: ReplyReader<T>,
    ): Promise<T> {
      const path = input.path.split('?')[0]!;
      const key = `${this.target} ${input.method} ${path}`;
      requests.push(key);
      if (this.target === OLD && path.endsWith('/switched'))
        identity_on_old.push(this.checks_identity);
      const route =
        routes[key] ??
        (path.endsWith('/file')
          ? () => state_file(new URL(input.path, 'http://x').searchParams.get('path')!)
          : undefined);
      if (!route) throw new Error(`unscripted ${key}`);
      const body = input.body
        ? JSON.parse(Buffer.from(input.body as Uint8Array).toString())
        : undefined;
      const answer = route(body);
      const response = new IncomingMessage(new Socket());
      response.statusCode = answer.status ?? 200;
      response.headers = { ...answer.headers };
      response.push(
        Buffer.isBuffer(answer.body) || typeof answer.body === 'string'
          ? Buffer.from(answer.body)
          : Buffer.from(JSON.stringify(answer.body)),
      );
      response.push(null);
      try {
        return await consume(response);
      } finally {
        response.destroy();
      }
    }
    override async close(): Promise<void> {}
  }
  const files = new FilesService();
  workers.push(files);
  const runner = new MacServerMove({
    root,
    machine_id: 'machine-b',
    platform: 'linux',
    home,
    files,
    authentication_headers: () => ({ 'X-Panels-Executor-ID': 'machine-b' }),
    server_address: () => OLD,
    server_session: () => new ScriptedSession(OLD, true),
    session: (address) => new ScriptedSession(address, false),
    save_server_address: async (address): Promise<MachineServerRecord> => {
      saved.push(address);
      return { server_url: address, server_id: 'server-1', highest_move_number: move_number };
    },
    extract: async (_archive, destination) => {
      await mkdir(join(destination, 'server/installation'), { recursive: true });
    },
    installer: async (_payload, request) => {
      installs.push(request);
      if (request.command === 'remove') {
        rmSync(request.root, { recursive: true, force: true });
        return '';
      }
      await mkdir(request.root, { recursive: true });
      await rename(request.data_directory, join(request.root, 'data'));
      return JSON.stringify({
        root: request.root,
        data_directory: join(request.root, 'data'),
        executor_link: link,
      });
    },
    log: () => {},
  });
  runners.push(runner);
  const install_root = join(home, '.local/share/panels-home');
  const work = join(root, 'server-move', MOVE);
  return {
    runner,
    root,
    work,
    link,
    install_root,
    files,
    reports,
    requests,
    identity_on_old,
    installs,
    saved,
  };
}
async function settle(runner: MacServerMove): Promise<void> {
  // A run, then the recovery pass that follows it.
  for (let index = 0; index < 3; index++) await runner.settled();
}

test('a committed move carries exactly the complete list and switches with the old server told plainly', async () => {
  const move = fixture();
  move.runner.start();
  await settle(move.runner);
  move.runner.notice({ move_id: MOVE });
  move.runner.notice({ move_id: MOVE });
  await settle(move.runner);
  const install = move.installs[0] as ServerMoveInstallRequest;
  expect(move.installs.map((request) => request.command)).toEqual(['install']);
  expect(install.held).toEqual({
    move_id: MOVE,
    target_machine_id: 'machine-b',
    server_url: 'https://new.example',
    server_id: 'server-1',
    move_number: 4,
  });
  // The file that the pause list dropped stays behind. The changed file has its paused bytes.
  expect((await readdir(join(move.install_root, 'data/state'))).sort()).toEqual(['a.json']);
  expect(readFileSync(join(move.install_root, 'data/state/a.json'), 'utf8')).toBe('{"a":2}');
  expect(readFileSync(join(move.install_root, 'data/planner.db'), 'utf8')).toBe('database copy');
  expect(move.reports).toEqual([{ state: 'checked' }]);
  expect(move.saved).toEqual([move.link]);
  expect(move.requests.filter((key) => key.endsWith('/held/commit'))).toHaveLength(1);
  expect(move.identity_on_old).toEqual([false]);
  expect(existsSync(move.work)).toBe(false);
});

test('a package that differs from its digest fails closed before any pause or install', async () => {
  const move = fixture({ package_bytes: Buffer.from('sealed server packagE') });
  move.runner.start();
  await settle(move.runner);
  move.runner.notice({ move_id: MOVE });
  await settle(move.runner);
  expect(move.reports).toEqual([
    { state: 'failed', reason: 'The server package does not match its digest.' },
  ]);
  expect(move.requests.some((key) => key.endsWith('/pause'))).toBe(false);
  expect(move.installs).toEqual([]);
  expect(existsSync(move.work)).toBe(false);
});

test('a refused commit removes the installed server and the move folder, and never switches', async () => {
  const move = fixture({ commit: false });
  move.runner.start();
  await settle(move.runner);
  move.runner.notice({ move_id: MOVE });
  await settle(move.runner);
  expect(move.installs.map((request) => request.command)).toEqual(['install', 'remove']);
  expect(move.reports).toEqual([
    { state: 'checked' },
    { state: 'failed', reason: 'The old server did not commit the move.' },
  ]);
  expect(move.saved).toEqual([]);
  expect(existsSync(move.install_root)).toBe(false);
  expect(existsSync(move.work)).toBe(false);
});

test('after a restart a committed leftover finishes its switch even though no server knows the run', async () => {
  const move = fixture();
  await mkdir(join(move.work, 'payload'), { recursive: true });
  await mkdir(move.install_root, { recursive: true });
  await writeFile(join(move.install_root, 'marker'), '');
  await atomic_record(move.files, join(move.work, 'run.json'), {
    instruction: {
      move_id: MOVE,
      phase: 'paused',
      server_url: 'https://new.example',
      port: 8800,
      server_id: 'server-1',
      move_number: 4,
      package: {
        platform: 'linux',
        revision: 'a'.repeat(40),
        archive_sha256: 'b'.repeat(64),
        archive_size: 1,
      },
      trusted_ingress: null,
    },
    old_address: OLD,
    installation: { root: move.install_root, service: 'com.panels.home', executor_link: move.link },
    committed: true,
  });
  move.runner.start();
  await settle(move.runner);
  expect(move.installs).toEqual([]);
  expect(move.saved).toEqual([move.link]);
  expect(existsSync(join(move.install_root, 'marker'))).toBe(true);
  expect(existsSync(move.work)).toBe(false);
});
