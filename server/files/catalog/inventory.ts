import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { dirname } from 'node:path';
import { foldDirectory } from '../logic/listing.ts';
import { symlink } from '../logic/paths.ts';
import type { ArtifactEntry, Inventory } from '../contracts.ts';
import * as config from './config.ts';
// Python's json.dumps uses spaces after separators, and recursive sorted keys.
export function pythonJson(value: unknown, sorted = false): string {
  if (Array.isArray(value)) return '[' + value.map((v) => pythonJson(v, sorted)).join(', ') + ']';
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value);
    if (sorted) entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return (
      '{' +
      entries.map(([k, v]) => JSON.stringify(k) + ': ' + pythonJson(v, sorted)).join(', ') +
      '}'
    );
  }
  return JSON.stringify(value);
}
const cache = new Map<string, Inventory>();
export async function inventory(root: string, prefix: string): Promise<Inventory> {
  const prior = cache.get(root),
    now = performance.now() / 1000;
  if (prior && now - prior.checked_at < config.INVENTORY_REFRESH_SECONDS) {
    cache.delete(root);
    cache.set(root, prior);
    return prior;
  }
  const result: Inventory = {
    checked_at: now,
    directories: Object.create(null) as Inventory['directories'],
    seen: new Set(),
    revision: '',
    bytes: 0,
  };
  function visit(entries: ArtifactEntry[], directory: string): void {
    result.directories[directory] = entries.map((item) => {
      const path = directory ? directory + '/' + item.name : item.name;
      const href = item.opens
        ? prefix + item.opens.split('/').map(encodeURIComponent).join('/')
        : null;
      if (href) result.seen.add(href);
      if (item.children.length) visit(item.children, path);
      const label =
        item.name.lastIndexOf('.') > 0 ? item.name.slice(0, item.name.lastIndexOf('.')) : item.name;
      return {
        key: 'managed:' + path,
        path: item.opens ?? path,
        label,
        kind: item.opens
          ? item.opens.split('/').at(-1)!.includes('.')
            ? item.opens.split('.').at(-1)!
            : 'file'
          : String(item.children.length),
        href,
        directory: item.children.length ? path : null,
        child_count: item.children.length,
      };
    });
  }
  let unsafe = false;
  for (let path = root; ; path = dirname(path)) {
    if (symlink(path)) unsafe = true;
    if (dirname(path) === path) break;
  }
  visit(unsafe ? [] : await foldDirectory(root), '');
  const encoded = Buffer.from(pythonJson(result.directories, true));
  result.revision = createHash('sha256').update(encoded).digest('hex');
  result.bytes = encoded.length;
  cache.delete(root);
  if (result.bytes <= config.INVENTORY_CACHE_MAX_BYTES) {
    cache.set(root, result);
    while (
      cache.size > config.INVENTORY_CACHE_OWNERS ||
      [...cache.values()].reduce((s, x) => s + x.bytes, 0) > config.INVENTORY_CACHE_MAX_BYTES
    )
      cache.delete(cache.keys().next().value!);
  }
  return result;
}
