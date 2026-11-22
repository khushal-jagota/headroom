/** Disposable disk storage owned by one Worker backend process. */
import { lstat, mkdir, readFile, readdir, realpath, rm } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { setTimeout as pause } from 'node:timers/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { OwnedChildProcesses } from '../lifecycle/contracts.ts';
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
async function disk_directory(root: string, processes: OwnedChildProcesses): Promise<void> {
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
      const child = processes.spawn('/sbin/mount', [], {
        env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin' },
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: false,
      });
      let text = '';
      const timeout = setTimeout(() => child.kill('SIGKILL'), 5000);
      child.stdout!.on('data', (chunk) => {
        text += String(chunk);
      });
      let failure: Error | undefined;
      child.once('error', (error) => {
        failure = error;
      });
      child.once('close', (code) => {
        clearTimeout(timeout);
        if (failure) reject(failure);
        else if (code === 0) resolve(text);
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
/** Another process can keep writing into a scratch folder after its Worker stops. Node's own
 * retry repeats at every folder level, so a deep writer holds it past the stop budget. One
 * pass per attempt keeps the whole wait inside this limit. */
const REMOVAL_LIMIT_MS = 1500;
const REMOVAL_RETRY_MS = 100;
const LISTED_ENTRIES = 20;
/** At most one entry past the listed ones, so a huge or linked tree cannot hold a stop. */
async function remaining_entries(directory: string): Promise<string[]> {
  const found: string[] = [];
  const pending = [''];
  while (pending.length > 0 && found.length <= LISTED_ENTRIES) {
    const relative = pending.shift()!;
    const entries = await readdir(join(directory, relative), { withFileTypes: true }).catch(
      () => [],
    );
    for (const entry of entries) {
      if (found.length > LISTED_ENTRIES) break;
      const path = join(relative, entry.name);
      found.push(path);
      if (entry.isDirectory()) pending.push(path);
    }
  }
  return found;
}
/** Disposable storage never fails its caller. A folder that remains stays for the next start or
 * retirement of its Ticket, with one warning that names what remained. */
async function remove_scratch(directory: string, limit: AbortSignal): Promise<boolean> {
  while (true) {
    try {
      await rm(directory, { recursive: true });
      return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
      if (limit.aborted) {
        const remaining = await remaining_entries(directory);
        const listed = remaining.slice(0, LISTED_ENTRIES).join(', ');
        const more = remaining.length > LISTED_ENTRIES ? ' and more' : '';
        console.warn(
          `Worker scratch folder stays in place, because it could not be deleted: ${directory} ` +
            `(${(error as Error).message}). Remaining entries: ${listed || 'none'}${more}`,
        );
        return false;
      }
    }
    await pause(REMOVAL_RETRY_MS);
  }
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
  const limit = AbortSignal.timeout(REMOVAL_LIMIT_MS);
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
    if (!(await remove_scratch(directory, limit))) complete = false;
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
    options: { processes: OwnedChildProcesses; home?: string; signal?: AbortSignal },
  ): Promise<WorkerScratch> {
    options.signal?.throwIfAborted();
    const pattern = ticket_name(environment.PLAN_TICKET_ID ?? '');
    const root = await prepare_root(options.home ?? homedir());
    await disk_directory(root, options.processes);
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
      TMUX_TMPDIR: directory,
      SCREENDIR: join(directory, 'screen'),
      TMPDIR: directory,
      TMP: directory,
      TEMP: directory,
    });
    try {
      await mkdir(join(directory, 'screen'), { mode: 0o700 });
      delete scratch.environment.TMUX;
      delete scratch.environment.STY;
      await prune(root, pattern, await readdir(root), directory);
      options.signal?.throwIfAborted();
      return scratch;
    } catch (error) {
      await scratch.close();
      throw error;
    }
  }
  /** All callers observe the removal attempt, including callers whose signal expired. A busy
   * folder that remains does not reject; the next start or retirement of the Ticket retries it. */
  close(): Promise<void> {
    if (this.closing) return this.closing;
    const attempt = (async () => {
      const info = await lstat(this.directory).catch((error: NodeJS.ErrnoException) => {
        if (error.code === 'ENOENT') return null;
        throw error;
      });
      if (info && !info.isSymbolicLink())
        await remove_scratch(this.directory, AbortSignal.timeout(REMOVAL_LIMIT_MS));
    })();
    this.closing = attempt;
    void attempt.catch(() => {
      if (this.closing === attempt) this.closing = null;
    });
    return attempt;
  }
}
