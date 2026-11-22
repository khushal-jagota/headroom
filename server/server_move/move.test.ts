/**
 * New boundaries, no existing coverage: the old server's pause and its complete list with a
 * database copy, the lift after a failure, restart recovery, and the held server's commit
 * and target-only admission on its local executor link. Reuses the Ticket database seed,
 * a private temporary server folder, and Fastify with one loopback and one socket listener.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { request as http_request } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import Fastify from 'fastify';
import { afterEach, expect, test } from 'vitest';
import { fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { loadConfig } from '../core/config.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { createDatabase, onlineBackup } from '../core/database/index.ts';
import { HttpError } from '../core/http/errors.ts';
import { open_executor_link } from '../core/http/executor_link.ts';
import { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { UpdateBarrier } from '../environments/update_barrier.ts';
import { sha256_file } from '../environments/server_state/checksums.ts';
import { seed_machine_lease } from '../machines/testing/lease.ts';
import { register_machine } from '../machines/executor_credentials.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { initialize_server_location, read_server_location } from '../server_link/data.ts';
import type { ServerMoveHeldMarker } from './contracts.ts';
import { SERVER_MOVE_ATTEMPT_FILE, SERVER_MOVE_HELD_FILE } from './contracts.ts';
import { HeldServerMove } from './held_service.ts';
import { startup_move_hold } from './hold.ts';
import type { ServerPackageCache } from './packages/cache.ts';
import { create_server_move_executor_routes } from './routes.ts';
import { ServerMoveCoordinator } from './service.ts';

const revision = 'c'.repeat(40);
const folders: string[] = [];
const databases: Database[] = [];
afterEach(() => {
  for (const database of databases.splice(0)) database.close();
  for (const folder of folders.splice(0)) rmSync(folder, { recursive: true, force: true });
});

async function server_folder() {
  const root = mkdtempSync(join(tmpdir(), 'panels-move-'));
  folders.push(root);
  const database_path = join(root, 'planner.db');
  await onlineBackup(fixture_path(fixture()), database_path);
  const database = createDatabase(database_path);
  databases.push(database);
  const location = initialize_server_location(database);
  const target = database.write((tx) => {
    const grant = register_machine(tx, 'Target', 'linux');
    seed_machine_lease(tx, grant.machine_id);
    tx.run(
      "UPDATE mac_executor_presence SET identity_json=json_set(identity_json,'$.application_revision',?) WHERE machine_id=?",
      [revision, grant.machine_id],
    );
    return grant;
  });
  const config = loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: database_path });
  config.app_sha = revision;
  for (const [path, text] of [
    ['files/tickets/t_1/artifacts/plan.md', 'plan'],
    ['skills/example/SKILL.md', 'skill'],
  ]) {
    mkdirSync(dirname(join(root, path!)), { recursive: true });
    writeFileSync(join(root, path!), text!);
  }
  const admission = new WorkspaceAdmission();
  const conversation = {
    workspace_admission: admission,
    reopen_after_update: () => admission.reopen(),
  } as unknown as ConversationSystem;
  const barrier = new UpdateBarrier(join(root, 'update-barrier.json'));
  return {
    root,
    database_path,
    database,
    location,
    target,
    config,
    admission,
    conversation,
    barrier,
  };
}
type Folder = Awaited<ReturnType<typeof server_folder>>;

function coordinator(folder: Folder, probe: (origin: string) => Promise<unknown>) {
  const archive = join(folder.root, 'package.tar.gz');
  writeFileSync(archive, 'package');
  const packages = {
    get: async () => ({
      platform: 'linux',
      revision,
      archive_sha256: 'a'.repeat(64),
      archive_size: 7,
    }),
    archive_path: () => archive,
  } as unknown as ServerPackageCache;
  return new ServerMoveCoordinator({
    database: folder.database,
    data_directory: folder.root,
    database_path: folder.database_path,
    state_paths: () => ({
      server_root: folder.root,
      database_path: folder.database_path,
      credential_key_path: join(folder.root, 'credential-secrets/key'),
    }),
    revision,
    trusted_ingress: null,
    update_barrier: folder.barrier,
    update_running: () => false,
    conversation: () => folder.conversation,
    packages,
    probe,
    emit_change: () => {},
  });
}
async function until(check: () => boolean) {
  for (let index = 0; index < 200 && !check(); index++) await new Promise((r) => setTimeout(r, 10));
  expect(check()).toBe(true);
}

test('the pause holds the server and lists the database copy; the commit and the switch make it the responder', async () => {
  const folder = await server_folder();
  let answer: unknown = { ...folder.location, server_url: 'https://new.example' };
  const move = coordinator(folder, async () => answer);
  const view = move.start(folder.target.machine_id, {
    server_url: 'https://new.example',
    port: 8767,
  });
  expect(() =>
    move.start(folder.target.machine_id, { server_url: 'https://new.example', port: 8767 }),
  ).toThrow(/Another server move/);
  await until(() => move.view()?.phase === 'copying');
  expect(move.notice(folder.target.machine_id)).toEqual({ move_id: view.move_id });
  expect(move.notice(HOME_MACHINE_ID)).toBeNull();
  await expect(move.files(HOME_MACHINE_ID, view.move_id)).rejects.toThrow(/No active server move/);
  const running = await move.files(folder.target.machine_id, view.move_id);
  expect(running.complete).toBe(false);
  expect(Object.keys(running.manifest)).toEqual([
    'files/tickets/t_1/artifacts/plan.md',
    'skills/example/SKILL.md',
  ]);
  expect(() => move.file(folder.target.machine_id, view.move_id, 'planner.db')).toThrow(
    /not listed/,
  );

  // A write after the copy is not in the list: the copy holds the record as it was paused.
  const files = await move.pause(folder.target.machine_id, view.move_id);
  expect(await move.pause(folder.target.machine_id, view.move_id)).toBe(files);
  expect(folder.barrier.frozen && folder.barrier.move_hold === view.move_id).toBe(true);
  expect(folder.admission.closed).toBe(true);
  expect(move.holds_work()).toBe(true);
  expect(move.update_refusal()).toMatch(/server move is running/);
  expect(files.complete).toBe(true);
  expect(Object.keys(files.manifest)).toEqual([
    'files/tickets/t_1/artifacts/plan.md',
    'planner.db',
    'skills/example/SKILL.md',
  ]);
  const database_file = move.file(folder.target.machine_id, view.move_id, 'planner.db');
  expect(database_file.path).not.toBe(folder.database_path);
  expect(await sha256_file(database_file.path)).toBe(files.manifest['planner.db']);

  answer = { ...folder.location, server_url: 'https://new.example', move_number: 9 };
  expect(await move.report(folder.target.machine_id, view.move_id, { state: 'checked' })).toEqual({
    commit: false,
  });
  expect(move.view()).toMatchObject({
    phase: 'failed',
    failure: expect.stringMatching(/does not answer/),
  });
  expect(
    folder.barrier.frozen || folder.barrier.move_hold !== null || folder.admission.closed,
  ).toBe(false);
  expect(existsSync(dirname(database_file.path))).toBe(false);

  // A second run commits, and the switch notice makes the record the address responder.
  answer = { ...folder.location, server_url: 'https://new.example' };
  const second = move.start(folder.target.machine_id, {
    server_url: 'https://new.example',
    port: 8767,
  });
  await until(() => move.view()?.phase === 'copying');
  await move.pause(folder.target.machine_id, second.move_id);
  expect(await move.report(folder.target.machine_id, second.move_id, { state: 'checked' })).toEqual(
    {
      commit: true,
    },
  );
  expect(JSON.parse(readFileSync(join(folder.root, SERVER_MOVE_ATTEMPT_FILE), 'utf8')).phase).toBe(
    'committing',
  );
  expect(startup_move_hold(folder.root)).toBe(second.move_id);
  expect(
    await move.report(folder.target.machine_id, second.move_id, {
      state: 'failed',
      reason: 'late',
    }),
  ).toEqual({
    commit: true,
  });
  await move.switched(folder.target.machine_id, second.move_id);
  expect(folder.database.read(read_server_location)).toEqual({
    ...folder.location,
    state: 'moved',
    server_url: 'https://new.example',
  });
  expect(move.view()?.phase).toBe('moved');
  expect(folder.barrier.frozen && move.holds_work()).toBe(true);
  await move.stop();
});

test('a restart fails a paused run and lifts nothing; a committing run stays held and follows the new address', async () => {
  const folder = await server_folder();
  let answer: unknown = { ...folder.location, server_url: 'https://new.example' };
  const first = coordinator(folder, async () => answer);
  const view = first.start(folder.target.machine_id, {
    server_url: 'https://new.example',
    port: 8767,
  });
  await until(() => first.view()?.phase === 'copying');
  await first.pause(folder.target.machine_id, view.move_id);
  await first.stop();

  const restarted_barrier = new UpdateBarrier(join(folder.root, 'update-barrier.json'));
  expect(startup_move_hold(folder.root)).toBeNull();
  const after_pause = coordinator({ ...folder, barrier: restarted_barrier }, async () => answer);
  after_pause.recover();
  expect(after_pause.view()).toMatchObject({
    phase: 'failed',
    failure: expect.stringMatching(/restarted/),
  });
  expect(after_pause.holds_work() || restarted_barrier.frozen).toBe(false);

  const second = after_pause.start(folder.target.machine_id, {
    server_url: 'https://new.example',
    port: 8767,
  });
  await until(() => after_pause.view()?.phase === 'copying');
  await after_pause.pause(folder.target.machine_id, second.move_id);
  await after_pause.report(folder.target.machine_id, second.move_id, { state: 'checked' });
  await after_pause.stop();

  // What server_services.setup() does with the startup hold, then the move's recovery.
  const held_barrier = new UpdateBarrier(join(folder.root, 'update-barrier.json'));
  held_barrier.move_hold = startup_move_hold(folder.root);
  held_barrier.close();
  answer = {
    ...folder.location,
    server_url: 'https://new.example',
    move_number: folder.location.move_number + 1,
  };
  const committing = coordinator({ ...folder, barrier: held_barrier }, async () => answer);
  committing.recover();
  expect(committing.view()?.phase).toBe('committing');
  await until(() => committing.view()?.phase === 'moved');
  expect(folder.database.read(read_server_location)?.state).toBe('moved');
  expect(held_barrier.frozen).toBe(true);
  await committing.stop();
});

function local_request(
  socket_path: string | null,
  port: number,
  path: string,
  credential: { machine_id: string; credential: string },
  body: unknown = {},
): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const request = http_request(
      {
        ...(socket_path === null ? { host: '127.0.0.1', port } : { socketPath: socket_path }),
        method: 'POST',
        path,
        headers: {
          'content-type': 'application/json',
          'x-panels-executor-id': credential.machine_id,
          'x-panels-executor-credential': credential.credential,
        },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('end', () =>
          resolve({
            status: response.statusCode!,
            body: JSON.parse(Buffer.concat(chunks).toString() || '{}'),
          }),
        );
      },
    );
    request.on('error', reject);
    request.end(JSON.stringify(body));
  });
}

test('a held server answers only its target on the local link, and its commit takes the next number in one write', async () => {
  const folder = await server_folder();
  const other = folder.database.write((tx) => register_machine(tx, 'Other', 'darwin'));
  const marker: ServerMoveHeldMarker = {
    move_id: 'move-1',
    target_machine_id: folder.target.machine_id,
    server_url: 'https://new.example',
    server_id: folder.location.server_id,
    move_number: folder.location.move_number,
  };
  writeFileSync(join(folder.root, SERVER_MOVE_HELD_FILE), JSON.stringify(marker));
  folder.barrier.move_hold = startup_move_hold(folder.root);
  folder.barrier.close();
  folder.admission.claim(() => {}, { idle: () => true, require_idle: false });
  const held = new HeldServerMove({
    database: folder.database,
    data_directory: folder.root,
    update_barrier: folder.barrier,
    conversation: () => folder.conversation,
  });
  held.recover();
  expect(folder.barrier.frozen).toBe(true);
  const app = Fastify();
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof HttpError) reply.code(error.statusCode).send({ detail: error.detail });
    else if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  await app.register(
    create_server_move_executor_routes(
      { config: folder.config, database: folder.database, data_directory: folder.root },
      coordinator(folder, async () => null),
      held,
    ),
  );
  await app.listen({ host: '127.0.0.1', port: 0 });
  const port = (app.server.address() as { port: number }).port;
  const socket_path = join(folder.root, 'executor-link/server.sock');
  const link = await open_executor_link(app.server, socket_path);
  try {
    const probe = '/api/executor/server-move/held/probe';
    expect((await local_request(null, port, probe, folder.target)).status).toBe(403);
    expect((await local_request(socket_path, port, probe, other)).status).toBe(403);
    const job = await local_request(socket_path, port, probe, folder.target);
    expect(job.status).toBe(200);
    const answer_path = `${probe}/${String(job.body.job_id)}`;
    expect(
      (await local_request(socket_path, port, answer_path, folder.target, { nonce: 'x' })).status,
    ).toBe(409);
    expect(
      (
        await local_request(socket_path, port, answer_path, folder.target, {
          nonce: job.body.nonce,
        })
      ).body,
    ).toEqual({ answered: true });
    const commit = '/api/executor/server-move/held/commit';
    expect(
      (await local_request(socket_path, port, commit, folder.target, { move_id: 'other' })).status,
    ).toBe(409);
    const reply = await local_request(socket_path, port, commit, folder.target, {
      move_id: 'move-1',
    });
    expect(reply.body).toEqual({
      server_id: folder.location.server_id,
      move_number: folder.location.move_number + 1,
    });
    expect(folder.database.read(read_server_location)).toEqual({
      ...folder.location,
      move_number: folder.location.move_number + 1,
      server_url: 'https://new.example',
    });
    expect(
      folder.database.read((sql) => sql.all('SELECT machine_id FROM machines WHERE is_home=1')),
    ).toEqual([{ machine_id: folder.target.machine_id }]);
    expect(existsSync(join(folder.root, SERVER_MOVE_HELD_FILE))).toBe(false);
    expect(
      folder.barrier.frozen || folder.barrier.move_hold !== null || folder.admission.closed,
    ).toBe(false);
    expect((await local_request(socket_path, port, probe, folder.target)).status).toBe(404);
  } finally {
    link?.close();
    await app.close();
  }

  // A commit that stopped before it removed its marker finishes at the next start.
  writeFileSync(join(folder.root, SERVER_MOVE_HELD_FILE), JSON.stringify(marker));
  const barrier = new UpdateBarrier(join(folder.root, 'update-barrier.json'));
  barrier.move_hold = startup_move_hold(folder.root);
  barrier.close();
  new HeldServerMove({
    database: folder.database,
    data_directory: folder.root,
    update_barrier: barrier,
    conversation: () => folder.conversation,
  }).recover();
  expect(barrier.frozen || existsSync(join(folder.root, SERVER_MOVE_HELD_FILE))).toBe(false);
});
