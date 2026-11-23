import { createHash } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { ScreenManifest, ScreenPayload } from './contracts';
import { MAX_SCREEN_BYTES, MAX_SCREEN_FILES } from './configuration';

export interface ScreenCopy { digest: string; appSha: string | null }
interface ScreenState {
  server: string | null;
  current: ScreenCopy | null;
  previous: ScreenCopy | null;
  pending: boolean;
  rejected: string | null;
  waiting: string | null;
}
const empty = (server: string | null): ScreenState => ({ server, current: null, previous: null, pending: false, rejected: null, waiting: null });
export const hash = (bytes: string | Buffer): string => createHash('sha256').update(bytes).digest('hex');
export const isDigest = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function validPath(path: string): boolean {
  return /^(web\/dist|assets|static)\/.+/.test(path) && !path.includes('\\') && !path.includes('\0') && path.split('/').every(part => part !== '.' && part !== '..' && part !== '');
}
function manifest(value: unknown, digest: string): ScreenManifest {
  const raw = value as ScreenManifest;
  if (!isDigest(digest) || raw?.version !== 1 || !Array.isArray(raw.files) || raw.files.length > MAX_SCREEN_FILES) throw new Error('Invalid screen manifest');
  let last = '';
  for (const file of raw.files) {
    if (typeof file?.path !== 'string' || !validPath(file.path) || file.path <= last || !isDigest(file.sha256)) throw new Error('Invalid screen file');
    last = file.path;
  }
  if (!raw.files.some(file => file.path === 'web/dist/index.html')) throw new Error('Missing screen index');
  const canonical: ScreenManifest = { version: 1, files: raw.files.map(file => ({ path: file.path, sha256: file.sha256 })) };
  if (hash(JSON.stringify(canonical)) !== digest) throw new Error('Screen digest mismatch');
  return canonical;
}
export function verifyPayload(value: unknown, digest: string): { manifest: ScreenManifest; files: Map<string, Buffer> } {
  const raw = value as ScreenPayload;
  const checked = manifest(raw?.manifest, digest);
  if (!raw.files || typeof raw.files !== 'object' || Array.isArray(raw.files) || Object.keys(raw.files).length !== checked.files.length) throw new Error('Invalid screen payload');
  const files = new Map<string, Buffer>();
  let size = 0;
  for (const file of checked.files) {
    const encoded = raw.files[file.path];
    if (typeof encoded !== 'string' || encoded.length > MAX_SCREEN_BYTES * 2) throw new Error('Invalid screen bytes');
    const bytes = Buffer.from(encoded, 'base64');
    size += bytes.length;
    if (size > MAX_SCREEN_BYTES || bytes.toString('base64') !== encoded || hash(bytes) !== file.sha256) throw new Error('Screen file mismatch');
    files.set(file.path, bytes);
  }
  return { manifest: checked, files };
}

/** One selected server owns the pointers. Other servers never inherit its screens. */
export class ScreenStore {
  private state: ScreenState;
  constructor(readonly folder: string, readonly baseline: string) {
    try { this.state = JSON.parse(readFileSync(join(folder, 'state.json'), 'utf8')) as ScreenState; }
    catch { this.state = empty(null); }
    const copy = (value: unknown): boolean => value === null || (typeof value === 'object' && value !== null && isDigest((value as ScreenCopy).digest) && ((value as ScreenCopy).appSha === null || typeof (value as ScreenCopy).appSha === 'string'));
    if (!this.state || typeof this.state !== 'object' || (this.state.server !== null && typeof this.state.server !== 'string') || !copy(this.state.current) || !copy(this.state.previous) || typeof this.state.pending !== 'boolean' || (this.state.rejected !== null && !isDigest(this.state.rejected)) || (this.state.waiting !== null && !isDigest(this.state.waiting))) this.state = empty(null);
  }
  private save(): void {
    mkdirSync(this.folder, { recursive: true });
    writeFileSync(join(this.folder, 'state.json.next'), JSON.stringify(this.state));
    renameSync(join(this.folder, 'state.json.next'), join(this.folder, 'state.json'));
  }
  select(server: string | null, recoverTrial = true): void {
    if (this.state.server !== server) this.state = empty(server);
    if (recoverTrial && this.state.pending) this.rollback();
    if (this.state.current && !this.verify(this.state.current.digest)) {
      this.state.rejected = this.state.current.digest;
      this.state.current = this.state.previous && this.verify(this.state.previous.digest) ? this.state.previous : null;
      this.state.previous = null;
    }
    this.save();
    this.cleanup();
  }
  get waiting(): string | null { return this.state.waiting; }
  set waiting(value: string | null) {
    if (value === this.state.waiting) return;
    this.state.waiting = value;
    this.save();
  }
  get current(): ScreenCopy | null { return this.state.current; }
  get pending(): boolean { return this.state.pending; }
  get rejected(): string | null { return this.state.rejected; }
  get root(): string { return this.state.current ? join(this.folder, this.state.current.digest) : this.baseline; }
  verify(digest: string): boolean {
    if (!isDigest(digest)) return false;
    try {
      const root = join(this.folder, digest);
      if (!lstatSync(root).isDirectory() || lstatSync(root).isSymbolicLink()) return false;
      const checked = manifest(JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8')), digest);
      const listed = new Set(checked.files.map(file => file.path));
      const walk = (folder: string, prefix = ''): void => {
        for (const entry of readdirSync(folder)) {
          const path = prefix + entry;
          const info = lstatSync(join(folder, entry));
          if (info.isSymbolicLink()) throw new Error('Linked screen file');
          if (info.isDirectory()) walk(join(folder, entry), path + '/');
          else if (!info.isFile() || (path !== 'manifest.json' && !listed.has(path))) throw new Error('Unexpected screen file');
        }
      };
      walk(root);
      let size = 0;
      for (const file of checked.files) {
        const bytes = readFileSync(join(root, file.path));
        size += bytes.length;
        if (size > MAX_SCREEN_BYTES || hash(bytes) !== file.sha256) return false;
      }
      return true;
    } catch { return false; }
  }
  install(value: unknown, digest: string): void {
    const checked = verifyPayload(value, digest);
    if (this.verify(digest)) return;
    const staging = join(this.folder, `.staging-${digest}`);
    rmSync(staging, { recursive: true, force: true });
    mkdirSync(staging, { recursive: true });
    try {
      for (const [path, bytes] of checked.files) {
        mkdirSync(dirname(join(staging, path)), { recursive: true });
        writeFileSync(join(staging, path), bytes);
      }
      writeFileSync(join(staging, 'manifest.json'), JSON.stringify(checked.manifest));
      const destination = join(this.folder, digest);
      if (existsSync(destination)) rmSync(destination, { recursive: true, force: true });
      renameSync(staging, destination);
    } catch (error) { rmSync(staging, { recursive: true, force: true }); throw error; }
  }
  activate(copy: ScreenCopy): void {
    if (!this.verify(copy.digest)) throw new Error('Screens are not verified');
    if (!this.state.pending) this.state.previous = this.state.current;
    this.state.current = copy;
    this.state.pending = true;
    this.save();
  }
  retryRejected(): void { this.state.rejected = null; this.save(); }
  ready(): void { this.state.pending = false; this.save(); this.cleanup(); }
  rollback(): void {
    if (this.state.current) this.state.rejected = this.state.current.digest;
    this.state.current = this.state.previous && this.verify(this.state.previous.digest) ? this.state.previous : null;
    this.state.previous = null;
    this.state.pending = false;
    this.save();
  }
  cleanup(retain: string[] = []): void {
    if (!existsSync(this.folder)) return;
    const keep = new Set([this.state.current?.digest, this.state.previous?.digest, this.waiting, ...retain]);
    for (const name of readdirSync(this.folder)) {
      if ((isDigest(name) && !keep.has(name)) || name.startsWith('.staging-')) rmSync(join(this.folder, name), { recursive: true, force: true });
    }
  }
}
