// Distinct risk: the Ticket detail read once carried a tree of the Ticket's whole folder. A folder with
// tens of thousands of files made the reply larger than the executor's 1 MiB bound, so
// every `panels ticket show` on that Ticket failed. No other test reads detail=full beside a large folder.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { registerArea } from '../core/authority/register.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { installCompatibility } from '../core/http/compatibility.ts';
import { MAX_ENVELOPE_BYTES } from '../executor/configuration.ts';
import { routes } from '../tickets/routes.ts';
import { create, fixture, fixture_path } from './ticket_fixture.ts';

test('detail=full does not grow with the Ticket folder', async () => {
  const database = fixture(),
    ticket = create(database),
    path = fixture_path(database);
  const owner = join(dirname(path), 'files', 'tickets', ticket.id);
  for (let directory = 0; directory < 200; directory++) {
    const folder = join(owner, 'artifacts', `group-${directory}`, 'nested');
    mkdirSync(folder, { recursive: true });
    for (let file = 0; file < 100; file++) writeFileSync(join(folder, `file-${file}.txt`), 'x');
  }
  const app = Fastify();
  installCompatibility(app);
  const config = loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: path });
  registerArea(app, { prefix: '/api', routes }, { database, config, clock: buildClock(config) });
  await app.ready();
  try {
    const response = await app.inject(`/api/tickets?detail=full&id=${ticket.id}`);
    expect(response.statusCode).toBe(200);
    expect(response.json()).not.toHaveProperty('artifacts');
    expect(Buffer.byteLength(response.body)).toBeLessThan(MAX_ENVELOPE_BYTES / 8);
  } finally {
    await app.close();
  }
}, 60_000);
