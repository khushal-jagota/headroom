/** A snapshot owns the exact bytes that its manifest describes. */
import { createHash } from 'node:crypto';
import { lstat, readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { ScreenManifest, ScreenMeta, ScreenPayload } from './contracts.ts';

const SCREEN_FOLDERS = ['web/dist', 'assets', 'static'] as const;

function sha256(bytes: string | Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

async function snapshot(root: string): Promise<ScreenPayload | null> {
  try {
    const index = await lstat(join(root, 'web/dist/index.html'));
    if (!index.isFile()) throw new Error('The screen index must be a regular file.');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
  if (!(await lstat(join(root, 'web'))).isDirectory())
    throw new Error('The screen web folder must be a regular directory.');
  const bytes = new Map<string, Buffer>();
  async function collect(relative: string): Promise<void> {
    const path = join(root, relative);
    const entry = await lstat(path);
    if (entry.isSymbolicLink()) throw new Error(`Screen snapshot refuses a link: ${relative}`);
    if (entry.isDirectory()) {
      for (const name of (await readdir(path)).sort()) {
        if (name.includes('\\')) throw new Error(`Invalid screen filename: ${name}`);
        await collect(`${relative}/${name}`);
      }
    } else if (entry.isFile()) bytes.set(relative, await readFile(path));
    else throw new Error(`Screen snapshot refuses a non-file: ${relative}`);
  }
  for (const folder of SCREEN_FOLDERS) {
    try {
      await lstat(join(root, folder));
    } catch (error) {
      // Some scripted builds omit the optional static folders.
      if ((error as NodeJS.ErrnoException).code === 'ENOENT' && folder !== 'web/dist') continue;
      throw error;
    }
    await collect(folder);
  }
  const paths = [...bytes.keys()].sort();
  const manifest: ScreenManifest = {
    version: 1,
    files: paths.map((path) => ({ path, sha256: sha256(bytes.get(path)!) })),
  };
  return {
    manifest,
    files: Object.fromEntries(paths.map((path) => [path, bytes.get(path)!.toString('base64')])),
  };
}

export function screen_digest(payload: ScreenPayload): string {
  return sha256(JSON.stringify(payload.manifest));
}

/** Only the installed root and the updater's admitted waiting root can supply screens. */
export class ScreenSnapshots {
  private readonly copies = new Map<string, Promise<ScreenPayload | null>>();
  private readonly installed_root: string;
  private readonly waiting_root: () => string | null;
  constructor(installed_root: string, waiting_root: () => string | null = () => null) {
    this.installed_root = installed_root;
    this.waiting_root = waiting_root;
  }

  private read(root: string): Promise<ScreenPayload | null> {
    let copy = this.copies.get(root);
    if (copy === undefined) {
      copy = snapshot(root).then(
        (value) => {
          if (value === null) this.copies.delete(root);
          return value;
        },
        (error: unknown) => {
          this.copies.delete(root);
          throw error;
        },
      );
      this.copies.set(root, copy);
    }
    return copy;
  }

  private async current_copies(): Promise<(ScreenPayload | null)[]> {
    const waiting = this.waiting_root();
    const roots = waiting === null ? [this.installed_root] : [this.installed_root, waiting];
    for (const root of this.copies.keys()) if (!roots.includes(root)) this.copies.delete(root);
    return Promise.all(
      roots.map((root) =>
        this.read(root).catch((error: unknown) => {
          // A waiting release can disappear when the installer swaps or retires it.
          if (root !== this.installed_root && (error as NodeJS.ErrnoException).code === 'ENOENT')
            return null;
          throw error;
        }),
      ),
    );
  }

  async meta(): Promise<ScreenMeta> {
    const [current, waiting] = await this.current_copies();
    return {
      screens: current ? { sha256: screen_digest(current) } : null,
      waiting_screens: waiting ? { sha256: screen_digest(waiting) } : null,
    };
  }

  async payload(digest: string): Promise<ScreenPayload | null> {
    if (!/^[a-f0-9]{64}$/.test(digest)) return null;
    const copies = await this.current_copies();
    return copies.find((copy) => copy !== null && screen_digest(copy) === digest) ?? null;
  }
}
