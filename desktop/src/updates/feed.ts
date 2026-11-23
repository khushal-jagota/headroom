// GitHub is confined to this adapter. Generic feeds let the proof use private signed builds.
import { app, net } from 'electron';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { desktopRelease, newestDesktopRelease } from './logic';
import type { DesktopRelease } from './contracts';

const REPOSITORY = 'khushal-jagota/headroom';

export async function updateFeed(): Promise<string> {
  const metadata = JSON.parse(await readFile(join(app.getAppPath(), 'package.json'), 'utf8'));
  if (metadata.panelsUpdateFeed !== undefined) {
    const feed = metadata.panelsUpdateFeed;
    if (feed?.provider !== 'generic' || typeof feed.url !== 'string') throw new Error('Invalid packaged proof feed');
    const url = new URL(feed.url);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid packaged proof feed URL');
    return url.href;
  }

  const releases: DesktopRelease[] = [];
  // GitHub returns creation order, which need not equal desktop version order.
  for (let page = 1; ; page++) {
    const response = await net.fetch(`https://api.github.com/repos/${REPOSITORY}/releases?per_page=100&page=${page}`, {
      headers: { Accept: 'application/vnd.github+json' },
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new Error(`Desktop release lookup failed: ${response.status}`);
    const entries: unknown = await response.json();
    if (!Array.isArray(entries)) throw new Error('Invalid desktop release list');
    for (const entry of entries) {
      if (typeof entry?.tag_name !== 'string' || typeof entry.draft !== 'boolean' || typeof entry.prerelease !== 'boolean') continue;
      const release = desktopRelease(entry.tag_name, entry.draft, entry.prerelease);
      if (release) releases.push(release);
    }
    if (entries.length < 100) break;
  }
  const newest = newestDesktopRelease(releases);
  if (newest === null) throw new Error('No stable desktop release exists');
  return `https://github.com/${REPOSITORY}/releases/download/${encodeURIComponent(newest.tag)}/`;
}
