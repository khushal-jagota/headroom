/** Linux evidence proves a readable owner, not authorship or hidden socket holders. */
import { execFile } from 'node:child_process';
import { readFile, readdir, readlink, realpath, stat, opendir } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import * as config from './config.ts';
import { PreviewError, type ProcessIdentity } from './contracts.ts';

type TcpRow = { local: string; remote: string; state: string; inode: number };

function code(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException).code;
}

function deadlineCheck(deadline: number): void {
  if (performance.now() >= deadline) {
    throw new PreviewError('Process inspection exceeded its deadline.', 503);
  }
}

function inspectionDeadline(): number {
  return performance.now() + config.INSPECTION_SECONDS * 1000;
}

function socketAddress(port: number): string {
  return `0100007F:${port.toString(16).toUpperCase().padStart(4, '0')}`;
}

export class LinuxProcessInspector {
  readonly proc: string;

  constructor(proc = '/proc') {
    this.proc = proc;
  }

  private async read(path: string, deadline: number): Promise<string> {
    deadlineCheck(deadline);
    let result: string;
    try {
      result = await readFile(path, 'utf8');
    } catch {
      throw new PreviewError('Process evidence is missing or unreadable.', 410);
    }
    deadlineCheck(deadline);
    return result;
  }

  private async identity(
    pid: number,
    inode: number,
    port: number,
    worktree: string,
    deadline: number,
  ): Promise<ProcessIdentity> {
    const root = join(this.proc, String(pid));
    const processStat = await this.read(join(root, 'stat'), deadline);
    let ticks: number;
    try {
      const close = processStat.lastIndexOf(')');
      if (close < 0) throw new Error('Malformed process stat');
      const value = processStat
        .slice(close + 2)
        .trim()
        .split(/\s+/)[19];
      if (value === undefined || !/^\d+$/.test(value)) throw new Error('Invalid process ticks');
      ticks = Number(value);
      const cwd = await realpath(join(root, 'cwd'));
      const inside = relative(worktree, cwd);
      if (inside === '..' || inside.startsWith(`..${sep}`) || isAbsolute(inside)) {
        throw new Error('Outside worktree');
      }
    } catch {
      throw new PreviewError('Listener cwd is outside the Ticket preview worktree.', 410);
    }
    return {
      boot_id: (await this.read(join(this.proc, 'sys/kernel/random/boot_id'), deadline)).trim(),
      pid,
      start_ticks: ticks,
      listener_inode: inode,
      port,
      worktree,
    };
  }

  private async fds(
    pid: number,
    deadline: number,
    budget: { count: number },
  ): Promise<Set<number>> {
    const inodes = new Set<number>();
    try {
      const root = join(this.proc, String(pid), 'fd');
      for await (const entry of await opendir(root)) {
        deadlineCheck(deadline);
        budget.count += 1;
        if (budget.count > config.DISCOVERY_FDS) {
          throw new PreviewError('Process descriptor inspection bound exceeded.', 503);
        }
        let target: string;
        try {
          target = await readlink(join(root, entry.name));
        } catch (error) {
          if (code(error) === 'ENOENT') continue;
          throw error;
        }
        const match = /^socket:\[(\d+)\]$/.exec(target);
        if (match) inodes.add(Number(match[1]));
      }
    } catch (error) {
      if (code(error) === 'ENOENT') return new Set();
      if (code(error) === 'EACCES' || code(error) === 'EPERM') {
        const refusal = new PreviewError('Cannot inspect process socket ownership.', 410);
        Object.defineProperty(refusal, 'cause', { value: error });
        throw refusal;
      }
      throw error;
    }
    return inodes;
  }

  private async tcp(deadline: number): Promise<TcpRow[]> {
    const result: TcpRow[] = [];
    for (const name of ['tcp', 'tcp6']) {
      const lines = (await this.read(join(this.proc, 'net', name), deadline)).trimEnd().split('\n');
      for (const line of lines.slice(1)) {
        deadlineCheck(deadline);
        const columns = line.trim().split(/\s+/);
        if (columns.length < 10 || !/^\d+$/.test(columns[9])) {
          throw new PreviewError('Malformed kernel socket evidence.', 410);
        }
        result.push({
          local: columns[1],
          remote: columns[2],
          state: columns[3],
          inode: Number(columns[9]),
        });
      }
    }
    return result;
  }

  async discover(port: number, worktree: string): Promise<ProcessIdentity> {
    const deadline = inspectionDeadline();
    const listeners = (await this.tcp(deadline)).filter(
      (row) => row.state === '0A' && Number.parseInt(row.local.split(':')[1], 16) === port,
    );
    if (listeners.length !== 1 || listeners[0].local.split(':')[0] !== '0100007F') {
      throw new PreviewError('Require exactly one listener bound to 127.0.0.1.');
    }
    const inode = listeners[0].inode;
    let pids: number[];
    try {
      pids = (await readdir(this.proc)).filter((name) => /^\d+$/.test(name)).map(Number);
    } catch {
      throw new PreviewError('Linux process evidence is unavailable.');
    }
    if (pids.length > config.DISCOVERY_PIDS) {
      throw new PreviewError('Process discovery bound exceeded.', 503);
    }
    const owners: number[] = [];
    const budget = { count: 0 };
    for (const pid of pids) {
      deadlineCheck(deadline);
      try {
        if ((await stat(join(this.proc, String(pid)))).uid !== process.getuid!()) continue;
      } catch (error) {
        if (code(error) === 'ENOENT') continue;
        throw error;
      }
      let owned: Set<number>;
      try {
        owned = await this.fds(pid, deadline, budget);
      } catch (error) {
        const cause = (error as Error).cause;
        if (cause && (code(cause) === 'EACCES' || code(cause) === 'EPERM')) continue;
        throw error;
      }
      if (owned.has(inode)) owners.push(pid);
    }
    if (owners.length !== 1) throw new PreviewError('Listener ownership is missing or shared.');
    const identity = await this.identity(owners[0], inode, port, worktree, deadline);
    await this.check(identity, deadline);
    return identity;
  }

  async check(identity: ProcessIdentity, deadline = inspectionDeadline()): Promise<void> {
    const observed = await this.identity(
      identity.pid,
      identity.listener_inode,
      identity.port,
      identity.worktree,
      deadline,
    );
    if (
      observed.boot_id !== identity.boot_id ||
      observed.pid !== identity.pid ||
      observed.start_ticks !== identity.start_ticks ||
      observed.listener_inode !== identity.listener_inode ||
      observed.port !== identity.port ||
      observed.worktree !== identity.worktree
    ) {
      throw new PreviewError('Preview process identity changed.', 410);
    }
    const listeners = (await this.tcp(deadline)).filter(
      (row) => row.state === '0A' && Number.parseInt(row.local.split(':')[1], 16) === identity.port,
    );
    if (
      listeners.length !== 1 ||
      listeners[0].local !== socketAddress(identity.port) ||
      listeners[0].inode !== identity.listener_inode
    ) {
      throw new PreviewError('Preview listener ended or changed.', 410);
    }
    if (!(await this.fds(identity.pid, deadline, { count: 0 })).has(identity.listener_inode)) {
      throw new PreviewError('Preview process no longer owns its listener.', 410);
    }
  }

  async connected(identity: ProcessIdentity, local_port: number): Promise<void> {
    const deadline = inspectionDeadline();
    for (;;) {
      await this.check(identity, deadline);
      const reverse = (await this.tcp(deadline)).filter(
        (row) =>
          row.local === socketAddress(identity.port) &&
          row.remote === socketAddress(local_port) &&
          row.state === '01',
      );
      if (
        reverse.length === 1 &&
        (await this.fds(identity.pid, deadline, { count: 0 })).has(reverse[0].inode)
      ) {
        await this.check(identity, deadline);
        return;
      }
      deadlineCheck(deadline);
      await delay(
        Math.min(config.ACCEPT_RETRY_SECONDS * 1000, Math.max(0, deadline - performance.now())),
      );
    }
  }
}

function git(args: string[], deadline: number): Promise<{ success: boolean; stdout: string }> {
  return new Promise((fulfill, reject) => {
    execFile(
      'git',
      args,
      { timeout: Math.max(1, Math.ceil(deadline - performance.now())), encoding: 'utf8' },
      (error, stdout) => {
        if (error && (error.killed || typeof error.code !== 'number')) {
          reject(error);
          return;
        }
        fulfill({ success: error === null, stdout });
      },
    );
  });
}

/** Require the reserved registered worktree, with no symlink component or bare repo. */
export async function validate_worktree(
  project_folder: string,
  ticket_id: string,
): Promise<string> {
  const deadline = inspectionDeadline();
  const folder = resolve(project_folder);
  const candidate = join(folder, 'worktrees', `${ticket_id}-preview`);
  try {
    if ((await realpath(folder)) !== folder || (await realpath(candidate)) !== candidate) {
      throw new PreviewError('Preview worktree path must not contain symlink components.');
    }
    const root = await git(['-C', candidate, 'rev-parse', '--show-toplevel'], deadline);
    if (!root.success || resolve(root.stdout.trim()) !== candidate) {
      throw new PreviewError('The reserved path must be a Git worktree root.');
    }
    const listed = await git(['-C', candidate, 'worktree', 'list', '--porcelain'], deadline);
    if (!listed.success || !listed.stdout.includes(`worktree ${candidate}\n`)) {
      throw new PreviewError('The reserved directory is not a registered Git worktree.');
    }
  } catch (error) {
    if (error instanceof PreviewError) throw error;
    throw new PreviewError('Cannot validate the Ticket preview Git worktree.');
  }
  return candidate;
}
