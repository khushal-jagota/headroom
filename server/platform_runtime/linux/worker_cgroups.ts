/** cgroup v2 custody remains independent of mutable process sessions. */
import { constants } from 'node:fs';
import type { BigIntStats } from 'node:fs';
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rmdir,
  stat,
  writeFile,
} from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, normalize, relative } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import type { CgroupIdentity, WorkerCgroupPrimitives } from '../../worker_supervision/contracts.ts';

export class WorkerCgroups implements WorkerCgroupPrimitives {
  readonly #root: string;
  readonly #self_cgroup: string;
  readonly #stat: (path: string) => Promise<Pick<BigIntStats, 'ino' | 'dev'>>;
  constructor(
    options: {
      root?: string;
      self_cgroup?: string;
      stat_identity?: (path: string) => Promise<Pick<BigIntStats, 'ino' | 'dev'>>;
    } = {},
  ) {
    this.#root = options.root ?? '/sys/fs/cgroup';
    this.#self_cgroup = options.self_cgroup ?? '/proc/self/cgroup';
    this.#stat = options.stat_identity ?? ((path) => stat(path, { bigint: true }));
  }
  async create(token: string): Promise<CgroupIdentity> {
    const membership = (await readFile(this.#self_cgroup, 'utf8'))
      .split('\n')
      .find((line) => line.startsWith('0::'));
    if (membership === undefined) throw new Error('Worker cgroup v2 membership is unavailable');
    const parent = join(this.#root, membership.slice(3).replace(/^\/+/, ''));
    if ((await readFile(join(parent, 'cgroup.subtree_control'), 'utf8')).trim())
      throw new Error('Worker parent cgroup enables controllers and requires a server leaf');
    const path = join(parent, `panels-worker-${token}`);
    await mkdir(path);
    try {
      try {
        await this.#stat(join(path, 'cgroup.kill'));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT')
          throw new Error('Worker cgroup.kill is unavailable');
        throw error;
      }
      const [root, group] = await Promise.all([this.#stat(this.#root), this.#stat(path)]);
      return { path, inode: group.ino, root_device: root.dev, root_inode: root.ino };
    } catch (error) {
      await rmdir(path);
      throw error;
    }
  }
  async #path(identity: CgroupIdentity): Promise<string> {
    const root = await this.#stat(this.#root);
    await readFile(join(this.#root, 'cgroup.controllers'));
    if (
      typeof identity.root_device !== 'bigint' ||
      typeof identity.root_inode !== 'bigint' ||
      root.dev !== identity.root_device ||
      root.ino !== identity.root_inode
    )
      throw new Error('Worker cgroup hierarchy identity does not match');
    const path = identity.path;
    if ((await realpath(this.#root)) !== this.#root)
      throw new Error('Worker cgroup identity does not match');
    if (
      typeof identity.inode !== 'bigint' ||
      identity.inode <= 0n ||
      !isAbsolute(path) ||
      normalize(path) !== path ||
      !path_is_within(this.#root, path) ||
      dirname(path) === this.#root ||
      !/^panels-worker-[0-9a-f]{32}$/.test(basename(path))
    )
      throw new Error('Worker cgroup identity does not match');
    // A missing suffix is allowed, but no existing component can redirect custody.
    let current = this.#root;
    for (const component of relative(this.#root, path).split('/')) {
      current = join(current, component);
      try {
        if ((await lstat(current)).isSymbolicLink())
          throw new Error('Worker cgroup identity does not match');
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') break;
        throw error;
      }
    }
    try {
      if ((await this.#stat(path)).ino !== identity.inode)
        throw new Error('Worker cgroup identity does not match');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    return path;
  }
  /** Read-only member inventory uses the same hierarchy and inode validation as cleanup. */
  async members(identity: CgroupIdentity): Promise<readonly number[]> {
    const path = await this.#path(identity);
    const result: number[] = [];
    const visit = async (directory: string): Promise<void> => {
      const contents = await readFile(join(directory, 'cgroup.procs'), 'utf8');
      for (const pid of contents.trim().split(/\s+/).filter(Boolean)) {
        if (!/^\d+$/.test(pid) || !Number.isSafeInteger(Number(pid)))
          throw new Error('Worker cgroup process identity is unreadable');
        result.push(Number(pid));
      }
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (entry.isSymbolicLink()) throw new Error('Worker cgroup member directory is a link');
        if (entry.isDirectory()) await visit(join(directory, entry.name));
      }
    };
    await visit(path);
    await this.#path(identity);
    return result;
  }
  async join(identity: CgroupIdentity): Promise<void> {
    await writeFile(join(await this.#path(identity), 'cgroup.procs'), String(process.pid), {
      flag: constants.O_WRONLY | constants.O_TRUNC,
    });
  }
  async kill(identity: CgroupIdentity): Promise<void> {
    try {
      await writeFile(join(await this.#path(identity), 'cgroup.kill'), '1', {
        flag: constants.O_WRONLY | constants.O_TRUNC,
      });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      try {
        await stat(await this.#path(identity));
      } catch (missing) {
        if ((missing as NodeJS.ErrnoException).code === 'ENOENT') return;
        throw missing;
      }
      throw error;
    }
  }
  async empty(identity: CgroupIdentity): Promise<boolean> {
    const path = await this.#path(identity);
    let contents: string;
    try {
      contents = await readFile(join(path, 'cgroup.events'), 'utf8');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      try {
        await stat(path);
      } catch (missing) {
        if ((missing as NodeJS.ErrnoException).code === 'ENOENT') return true;
        throw missing;
      }
      throw error;
    }
    const fields = new Map(
      contents
        .trim()
        .split('\n')
        .map((line): [string, string] => {
          const parts = line.trim().split(/\s+/);
          if (parts.length !== 2)
            throw new Error('Worker cgroup population evidence is unavailable');
          return [parts[0], parts[1]];
        }),
    );
    const populated = fields.get('populated');
    if (populated !== '0' && populated !== '1')
      throw new Error('Worker cgroup population evidence is unavailable');
    return populated === '0';
  }
  async remove(identity: CgroupIdentity): Promise<void> {
    await rmdir(await this.#path(identity));
  }
}
