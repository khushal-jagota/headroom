import { mkdirSync, writeFileSync, utimesSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { monitorEventLoopDelay, performance } from 'node:perf_hooks';
import { expect, test } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { routes } from '../files/routes.ts';
// The catalog once walked and hashed the whole owner folder on every call, so one large
// Ticket folder held its file list for seconds. HTTP injection proves the route's cost
// follows the listed folder, and that the per-folder revision still sees a nested edit.
test('the file catalog reads one folder, whatever the size of the owner folder', async () => {
  const database = fixture(),
    ticket = create(database),
    db = fixture_path(database);
  const root = join(dirname(db), 'files', 'tickets', ticket.id);
  // About 24k entries in deep repository-shaped folders. The whole-tree catalog took about
  // 1.5 s per call on this; the one-folder catalog takes a few milliseconds.
  for (let repo = 0; repo < 4; repo++)
    for (let pkg = 0; pkg < 100; pkg++) {
      const lib = join(root, 'work', `r${repo}`, 'node_modules', `p${pkg}`, 'dist', 'lib');
      mkdirSync(lib, { recursive: true });
      for (let i = 0; i < 50; i++) writeFileSync(join(lib, `m${i}.js`), '');
    }
  mkdirSync(join(root, 'artifacts', 'site'), { recursive: true });
  mkdirSync(join(root, 'artifacts', 'nested', 'deeper'), { recursive: true });
  writeFileSync(join(root, 'artifacts', 'plan.html'), '');
  writeFileSync(join(root, 'artifacts', 'site', 'index.html'), '');
  writeFileSync(join(root, 'artifacts', 'site', 'asset.png'), '');
  writeFileSync(join(root, 'artifacts', 'nested', 'deeper', 'note.md'), '');
  const prefix = `/files/tickets/${ticket.id}/`;
  database.write((sql) =>
    // Listed files and the page itself are not repeated as links. A file inside the
    // page and a missing deep file are shown as links.
    [
      'artifacts/plan.html',
      'artifacts/site/index.html',
      'artifacts/site/asset.png',
      'artifacts/nested/deeper/later.md',
    ].forEach((path, index) =>
      sql.run('INSERT INTO ticket_artifact_links VALUES (?,?,?,?,?,?,?,?,?)', [
        ticket.id,
        'brief',
        index,
        prefix + path,
        prefix + path,
        null,
        path.split('/').at(-1)!.split('.')[0]!,
        '',
        path.split('.').at(-1)!,
      ]),
    ),
  );
  const config = loadConfig(undefined, { PLAN_DB_PATH: db, PLAN_TEST_MODE: '1' });
  const app = await createApp(config, { database, areas: [{ prefix: '', routes }] });
  const read = async (directory: string) => {
    const response = await app.inject({
      url: `/api/artifact-catalog/ticket/${ticket.id}?directory=${encodeURIComponent(directory)}`,
    });
    expect(response.statusCode).toBe(200);
    return JSON.parse(response.body) as {
      revision: string;
      total: number;
      entries: { path: string; directory: string | null; child_count: number }[];
    };
  };
  try {
    const loop = monitorEventLoopDelay({ resolution: 1 });
    loop.enable();
    const start = performance.now();
    const top = await read('');
    const elapsed = performance.now() - start;
    loop.disable();
    expect(elapsed).toBeLessThan(500);
    expect(loop.max / 1e6).toBeLessThan(100);
    expect(top.entries.map((row) => row.path)).toEqual([
      'artifacts',
      'work',
      'artifacts/site/asset.png',
      'artifacts/nested/deeper/later.md',
    ]);
    const artifacts = await read('artifacts');
    expect(artifacts.entries.map((row) => [row.path, row.directory, row.child_count])).toEqual(
      expect.arrayContaining([
        ['artifacts/site/index.html', null, 0],
        ['artifacts/nested', 'artifacts/nested', 1],
      ]),
    );
    expect((await read('artifacts/site')).total).toBe(0);
    const deeper = await read('artifacts/nested/deeper');
    expect(deeper.entries.map((row) => row.path)).toEqual(['artifacts/nested/deeper/note.md']);
    utimesSync(join(root, 'artifacts', 'nested', 'deeper', 'note.md'), 2000, 2000);
    expect((await read('artifacts/nested/deeper')).revision).not.toBe(deeper.revision);
    // The deep linked file appears: the root rows stay the same, but its link row leaves,
    // so the root revision must change for a client that pages the root.
    writeFileSync(join(root, 'artifacts', 'nested', 'deeper', 'later.md'), '');
    const after = await read('');
    expect(after.total).toBe(top.total - 1);
    expect(after.revision).not.toBe(top.revision);
  } finally {
    await app.close();
  }
});
