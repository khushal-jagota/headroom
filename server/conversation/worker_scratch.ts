/** Disposable disk storage owned by one Worker backend process. */
import { lstat, mkdir, readFile, readdir, realpath, rm } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { path_is_within } from '../core/path_containment.ts';

function ticket_name(ticket_id: string): RegExp {
  if (!/^[A-Za-z0-9_-]+$/.test(ticket_id)) throw new Error('Invalid Ticket identity for scratch');
  return new RegExp(`^${ticket_id}-[0-9a-f]{6}$`);
}
async function prepare_root(home: string): Promise<string> {
  const root = join(home, '.pw');
  await mkdir(root, { recursive: true, mode: 0o700 });
  const info = await lstat(root);
  if (!info.isDirectory() || info.uid !== process.getuid?.() || info.mode & 0o077)
    throw new Error(`Worker scratch root must be a private real directory: ${root}`);
  return root;
}
async function disk_directory(root: string): Promise<void> {
  let kind: string | undefined;
  if (process.platform === 'linux') {
    const resolved = await realpath(root);
    let deepest = -1;
    for (const line of (await readFile('/proc/self/mountinfo', 'utf8')).trimEnd().split('\n')) {
      const [before, after] = line.split(' - ');
      if (!before || !after) continue;
      const mount = before
        .split(' ')[4]
        ?.replace(/\\([0-7]{3})/g, (_, octal: string) => String.fromCharCode(parseInt(octal, 8)));
      if (mount && path_is_within(mount, resolved) && mount.length >= deepest) {
        deepest = mount.length;
        kind = after.split(' ')[0];
      }
    }
  } else if (process.platform === 'darwin') {
    const output = await new Promise<string>((resolve, reject) => {
      const child = new ChildProcesses().spawn('/sbin/mount', [], {
        env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin' },
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: false,
      });
      let text = '';
      const timeout = setTimeout(() => child.kill('SIGKILL'), 5000);
      child.stdout!.on('data', (chunk) => {
        text += String(chunk);
      });
      child.once('error', reject);
      child.once('close', (code) => {
        clearTimeout(timeout);
        if (code === 0) resolve(text);
        else reject(new Error('Cannot inspect the Mac scratch filesystem'));
      });
    });
    const resolved = await realpath(root);
    let deepest = -1;
    for (const line of output.split('\n')) {
      const match = /.+ on (.+) \(([^,)]+).+\)/.exec(line);
      if (match && path_is_within(match[1]!, resolved) && match[1]!.length >= deepest) {
        deepest = match[1]!.length;
        kind = match[2];
      }
    }
  }
  if (!kind || ['tmpfs', 'ramfs', 'devfs', 'mfs'].includes(kind))
    throw new Error(`Worker temporary storage must use disk: ${root}`);
}
async function root_exists(home: string): Promise<boolean> {
  try {
    await lstat(join(home, '.pw'));
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}
async function prune(
  root: string,
  pattern: RegExp,
  names: readonly string[],
  keep?: string,
): Promise<boolean> {
  let complete = true;
  for (const name of names) {
    const directory = join(root, name);
    if (!pattern.test(name) || directory === keep) continue;
    const info = await lstat(directory).catch((error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    if (!info) continue;
    if (!info.isDirectory() || info.uid !== process.getuid?.()) {
      complete = false;
      continue;
    }
    await rm(directory, { recursive: true });
  }
  return complete;
}
export async function snapshot_ticket_scratch(
  ticket_id: string,
  home = homedir(),
): Promise<string[]> {
  const pattern = ticket_name(ticket_id);
  if (!(await root_exists(home))) return [];
  return (await readdir(await prepare_root(home))).filter((name) => pattern.test(name)).sort();
}
export async function prune_ticket_scratch(
  ticket_id: string,
  names: readonly string[],
  home = homedir(),
): Promise<boolean> {
  const pattern = ticket_name(ticket_id);
  if (names.some((name) => !pattern.test(name))) return false;
  if (!(await root_exists(home))) return true;
  return prune(await prepare_root(home), pattern, names);
}
export class WorkerScratch {
  private closing: Promise<void> | null = null;
  readonly directory: string;
  readonly environment: Record<string, string>;
  private constructor(directory: string, environment: Record<string, string>) {
    this.directory = directory;
    this.environment = environment;
  }
  static async create(
    environment: Readonly<Record<string, string>>,
    options: { home?: string; signal?: AbortSignal } = {},
  ): Promise<WorkerScratch> {
    options.signal?.throwIfAborted();
    const pattern = ticket_name(environment.PLAN_TICKET_ID ?? '');
    const root = await prepare_root(options.home ?? homedir());
    await disk_directory(root);
    let directory: string;
    while (true) {
      directory = join(root, `${environment.PLAN_TICKET_ID}-${randomBytes(3).toString('hex')}`);
      try {
        await mkdir(directory, { mode: 0o700 });
        break;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      }
    }
    const scratch = new WorkerScratch(directory, {
      ...environment,
      TMPDIR: directory,
      TMP: directory,
      TEMP: directory,
    });
    try {
      await prune(root, pattern, await readdir(root), directory);
      options.signal?.throwIfAborted();
      return scratch;
    } catch (error) {
      await scratch.close();
      throw error;
    }
  }
  /** All callers observe actual removal, including callers whose signal expired. */
  close(): Promise<void> {
    if (this.closing) return this.closing;
    const attempt = (async () => {
      const info = await lstat(this.directory).catch((error: NodeJS.ErrnoException) => {
        if (error.code === 'ENOENT') return null;
        throw error;
      });
      if (info && !info.isSymbolicLink()) await rm(this.directory, { recursive: true });
    })();
    this.closing = attempt;
    void attempt.catch(() => {
      if (this.closing === attempt) this.closing = null;
    });
    return attempt;
  }
}
