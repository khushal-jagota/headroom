import { createHash } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { load_machine_baseline, machine_baseline_pin } from './machine_baselines.ts';

// ZIP parsing has its own boundary. These tests exercise source selection and digest custody.
vi.mock('../updates/zip.ts', () => ({
  with_zip_member: async (_path: string, _name: string, consume: (stream: Readable) => unknown) =>
    consume(Readable.from(['inner archive'])),
}));
const roots: string[] = [];
afterEach(async () => {
  vi.unstubAllGlobals();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});
const digest = (text: string) => createHash('sha256').update(text).digest('hex');
const zip_bytes = 'release ZIP';
const pin = {
  source_sha: 'a'.repeat(40),
  zip_sha256: digest(zip_bytes),
  archive_sha256: digest('inner archive'),
  archive_size: Buffer.byteLength('inner archive'),
};
const release = {
  tag_name: 'server-newest',
  draft: false,
  prerelease: false,
  published_at: '2026-11-25T00:00:00Z',
  assets: [
    { id: 1, name: 'publication.json', browser_download_url: 'https://example.invalid/receipt' },
    {
      id: 2,
      name: 'linux-executor.zip',
      digest: 'sha256:' + pin.zip_sha256,
      browser_download_url: 'https://example.invalid/archive',
    },
  ],
};
async function root() {
  const value = await mkdtemp(join(tmpdir(), 'machine-baseline-'));
  roots.push(value);
  return value;
}
function receipt(value = pin) {
  return { manifest: { app_sha: value.source_sha }, linux_executor: value };
}
function serve(selected = release, publication = receipt(), archive = zip_bytes) {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      if (url.includes('/releases?'))
        return Response.json([
          { ...release, tag_name: 'desktop-newer', published_at: '2026-12-01T00:00:00Z' },
          { ...release, prerelease: true, published_at: '2026-12-02T00:00:00Z' },
          { ...release, draft: true, published_at: '2026-12-03T00:00:00Z' },
          {
            ...release,
            tag_name: 'server-older',
            published_at: '2026-11-24T00:00:00Z',
            assets: [],
          },
          selected,
        ]);
      if (url.includes('/releases/tags/')) return Response.json(selected);
      if (url.endsWith('/releases/assets/1')) return Response.json(publication);
      if (url.endsWith('/releases/assets/2')) return new Response(archive);
      throw new Error('Unexpected baseline source: ' + url);
    }),
  );
  return urls;
}
test('previous baseline uses the newest permanent server Release and its checked archive', async () => {
  const urls = serve();
  const result = await load_machine_baseline(
    'linux',
    'previous',
    await root(),
    'example/panels',
    '',
    new AbortController().signal,
  );
  expect(result).toMatchObject(pin);
  expect(urls).toEqual([
    'https://api.github.com/repos/example/panels/releases?per_page=100&page=1',
    'https://api.github.com/repos/example/panels/releases/assets/1',
    'https://api.github.com/repos/example/panels/releases/assets/2',
  ]);
});
test('floor Release metadata cannot redefine the committed floor', async () => {
  const urls = serve();
  await expect(
    load_machine_baseline(
      'linux',
      'oldest',
      await root(),
      'example/panels',
      '',
      new AbortController().signal,
    ),
  ).rejects.toThrow('floor differs from its retained pin');
  expect(urls[0]).toBe(
    'https://api.github.com/repos/example/panels/releases/tags/server-54aede8564ee693d178cae55d3acb04ca9d770f4-45315260518-1',
  );
  expect(urls).toHaveLength(2);
  const floor = await machine_baseline_pin('linux');
  serve(
    {
      ...release,
      assets: [release.assets[0]!, { ...release.assets[1]!, digest: 'sha256:' + floor.zip_sha256 }],
    },
    receipt(floor),
  );
  await expect(
    load_machine_baseline(
      'linux',
      'oldest',
      await root(),
      'example/panels',
      '',
      new AbortController().signal,
    ),
  ).rejects.toThrow('ZIP digest differs');
});
test('a Release baseline rejects corrupt ZIP and inner archive bytes', async () => {
  serve(release, receipt(), 'corrupt ZIP');
  await expect(
    load_machine_baseline(
      'linux',
      'previous',
      await root(),
      'example/panels',
      '',
      new AbortController().signal,
    ),
  ).rejects.toThrow('ZIP digest differs');
  serve(release, receipt({ ...pin, archive_sha256: 'b'.repeat(64) }));
  await expect(
    load_machine_baseline(
      'linux',
      'previous',
      await root(),
      'example/panels',
      '',
      new AbortController().signal,
    ),
  ).rejects.toThrow('archive differs');
});
