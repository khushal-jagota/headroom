import { gunzipSync } from 'node:zlib';
import { mkdirSync, writeFileSync, symlinkSync, utimesSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { routes } from '../files/routes.ts';
// Direct agent filesystem writes bypass database revisions. HTTP injection proves
// validators describe delivered bytes and HTML reads respect real symlink targets.
// The existing video browser test separately proves the browser's media stack.
test('inline managed files retain byte validators and their owner boundary', async () => {
  const database = fixture(),
    ticket = create(database),
    db = fixture_path(database);
  const root = join(dirname(db), 'files', 'tickets', ticket.id),
    site = join(root, 'site');
  mkdirSync(site, { recursive: true });
  const document = join(site, 'index.html');
  writeFileSync(document, '<h1>One</h1>');
  writeFileSync(join(root, 'outside.html'), '<h1>Outside</h1>');
  symlinkSync(join(root, 'outside.html'), join(site, 'escape.html'));
  const foreign = join(dirname(db), 'foreign.html');
  writeFileSync(foreign, '<h1>Foreign</h1>');
  symlinkSync(foreign, join(site, 'foreign.html'));
  const config = loadConfig(undefined, { PLAN_DB_PATH: db, PLAN_TEST_MODE: '1' });
  const app = await createApp(config, { database, areas: [{ prefix: '', routes }] });
  try {
    // Python's final 64 KiB range chunk is a complete body, so gzip retains length.
    writeFileSync(join(root, 'boundary.bin'), Buffer.alloc(64 * 1024 + 1));
    const boundary = await app.inject({
      url: `/files/tickets/${ticket.id}/boundary.bin`,
      headers: { range: 'bytes=0-65535', 'accept-encoding': 'gzip' },
    });
    expect(boundary.statusCode).toBe(206);
    expect(Number(boundary.headers['content-length'])).toBe(boundary.rawPayload.length);
    expect(gunzipSync(boundary.rawPayload).equals(Buffer.alloc(64 * 1024))).toBe(true);
    const url = `/files/tickets/${ticket.id}/site/index.html`;
    const first = await app.inject({ url });
    expect(first.statusCode).toBe(200);
    expect(first.headers['content-disposition']).toBe('inline; filename="index.html"');
    expect(first.headers['x-content-type-options']).toBeUndefined();
    expect(first.headers['cache-control']).toBe('private, no-cache');
    const head = await app.inject({ method: 'HEAD', url });
    expect(head.statusCode).toBe(200);
    expect(head.body).toBe('');
    for (const header of ['content-type', 'content-length', 'content-disposition', 'etag'])
      expect(head.headers[header]).toBe(first.headers[header]);
    const cached = await app.inject({
      url,
      headers: { 'if-none-match': String(first.headers.etag) },
    });
    expect(cached.statusCode).toBe(304);
    expect(cached.headers['content-disposition']).toBeUndefined();
    expect(cached.headers['x-content-type-options']).toBeUndefined();
    utimesSync(document, 1000, 1000);
    writeFileSync(document, '<h1>Two</h1>');
    utimesSync(document, 1000, 1000);
    const changed = await app.inject({
      url,
      headers: { 'if-none-match': String(first.headers.etag) },
    });
    expect(changed.statusCode).toBe(200);
    expect(changed.body).toBe('<h1>Two</h1>');
    expect(changed.headers.etag).not.toBe(first.headers.etag);
    const escaped = await app.inject({
      url: `/files/tickets/${ticket.id}/site/escape.html`,
      headers: { 'x-panels-artifact-root': url },
    });
    expect(escaped.statusCode).toBe(200);
    expect(escaped.body).toBe('<h1>Outside</h1>');
    const outsideOwner = await app.inject({
      url: `/files/tickets/${ticket.id}/site/foreign.html`,
    });
    expect(outsideOwner.statusCode).toBe(404);
    const unsafe = await app.inject({ url: `/files/tickets/${ticket.id}/site%252findex.html` });
    expect(unsafe.statusCode).toBe(404);
  } finally {
    await app.close();
  }
});
