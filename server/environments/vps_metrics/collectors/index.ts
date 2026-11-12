/** This factory is selected explicitly and constructed inside the sampler worker. */
import { access, readFile, readdir, stat } from 'node:fs/promises';
import type { BuildCollector } from '../sampler/contracts.ts';
import type { CollectorPaths, CollectorDependencies } from './contracts.ts';
import { LinuxCollector } from './linux.ts';
import { clock_ticks, page_size, statvfs, monotonic_seconds } from './platform.ts';
export { monotonic_seconds } from './platform.ts';
import { query_services } from './systemd.ts';
export function filesystem_evidence(): Pick<
  CollectorDependencies,
  'read_text' | 'directory' | 'exists' | 'inode'
> {
  return {
    read_text: async (path) => (await readFile(path, 'utf8')).replace(/\r\n?/g, '\n'),
    directory: readdir,
    exists: async (path) => {
      try {
        await access(path);
        return true;
      } catch (error) {
        if (
          error instanceof Error &&
          'code' in error &&
          ['ENOENT', 'ENOTDIR'].includes(String(error.code))
        )
          return false;
        throw error;
      }
    },
    inode: async (path) => (await stat(path, { bigint: true })).ino,
  };
}
export const build_linux_collector: BuildCollector = (raw, config) =>
  new LinuxCollector(
    config,
    {
      proc_root: '/proc',
      sys_root: '/sys',
      cgroup_root: '/sys/fs/cgroup',
      ...(raw as Partial<CollectorPaths>),
    },
    {
      ...filesystem_evidence(),
      wall_clock: () => Date.now() / 1000,
      monotonic: monotonic_seconds,
      clock_ticks,
      page_size,
      statvfs,
      service_query: query_services,
    },
  );
