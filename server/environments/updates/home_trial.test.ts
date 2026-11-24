import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { expect, it } from 'vitest';
import { createApp } from '../../app.ts';
import { loadConfig } from '../../core/config.ts';
import { executor_link_socket_path, open_executor_link } from '../../core/http/executor_link.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../../execution_configuration.ts';
import { write_credential } from '../../executor/credential_file.ts';
import { MacHttpClient } from '../../executor/mac/transport/http.ts';
import {
  issue_home_machine_credential,
  register_machine,
} from '../../machines/executor_credentials.ts';
import { fixture, fixture_path } from '../../tests/ticket_fixture.ts';
import { UpdateBarrier } from '../update_barrier.ts';
import { attempt_path } from './attempts.ts';

it('requires fresh authenticated local traffic from the retained machine before accepting this attempt', async () => {
  const database = fixture(),
    path = fixture_path(database),
    root = dirname(path);
  const previous = 'a'.repeat(40),
    candidate = 'b'.repeat(40),
    attempt_id = 'trial-a';
  const grant = database.write((tx) => issue_home_machine_credential(tx, 'linux'));
  const barrier = join(root, 'update-barrier.json');
  writeFileSync(barrier, attempt_id);
  writeFileSync(
    attempt_path(root),
    JSON.stringify({ attempt_id, to_sha: candidate, state: 'installing' }),
  );
  const app = await createApp(
    loadConfig(undefined, {
      PLAN_DB_PATH: path,
      PLAN_TEST_MODE: '1',
      PLAN_APP_SHA: candidate,
    }),
    { database, updateBarrier: new UpdateBarrier(barrier) },
  );
  await app.ready();
  const socket = executor_link_socket_path(path);
  const listener = await open_executor_link(app.server, socket);
  const identity = {
    protocol_version: PROTOCOL_VERSION,
    application_revision: previous,
    spool_format_version: SPOOL_FORMAT_VERSION,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    connection_generation: 0,
  };
  const client = new MacHttpClient(socket, grant.machine_id, identity);
  const otherGrant = database.write((tx) => register_machine(tx, 'Extra machine', 'linux'));
  const otherClient = new MacHttpClient(socket, otherGrant.machine_id, identity);
  const lease = {
    identity,
    process_id: 1,
    mac_wall_clock_ms: Date.now(),
    queue_depth: 0,
    oldest_item_age_ms: null,
    waiting_conversations: [],
    waiting_conversations_truncated: false,
    working_turns: [],
    admission_stopped: false,
  };
  const health = () => app.inject({ url: '/api/health?expected_sha=' + candidate });
  try {
    expect(listener).not.toBeNull();
    expect((await health()).statusCode).toBe(503);
    await expect(client.lease(lease)).rejects.toThrow();
    expect((await health()).statusCode).toBe(503);
    const otherSecret = join(root, 'extra-credential');
    await write_credential(otherSecret, otherGrant.credential);
    await otherClient.ensure_credential(otherSecret);
    for (let i = 0; i < 2; i++) {
      expect(await otherClient.lease(lease)).toMatchObject({ error: { code: 'retry_later' } });
    }
    expect((await health()).statusCode).toBe(503);
    const secret = join(root, 'machine-credential');
    await write_credential(secret, grant.credential);
    await client.ensure_credential(secret);
    expect(await client.lease(lease)).toMatchObject({ error: { code: 'retry_later' } });
    expect((await health()).statusCode).toBe(503);
    expect(await client.lease(lease)).toMatchObject({ error: { code: 'retry_later' } });
    expect((await health()).json()).toEqual({
      app_sha: candidate,
      ready: true,
      prepared_attempt_id: attempt_id,
    });
  } finally {
    await otherClient.stop({ deadline: performance.now() / 1000 + 5 });
    await client.stop({ deadline: performance.now() / 1000 + 5 });
    listener?.close();
    await app.close();
  }
});
