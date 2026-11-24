/** Read-only inventory of an exact historical Panels service unit. */
import { readFile, readdir, realpath, stat } from 'node:fs/promises';
import { basename, join, resolve } from 'node:path';
import { path_is_within } from '../core/path_containment.ts';
import { parse_linux_worker_process } from '../platform_runtime/linux/worker_processes.ts';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';
import { BackendCommands } from './snapshot/commands.ts';

const UNIT = /^panels-worker-[0-9a-f]{32}\.service$/;
const PROPERTIES = '--property=LoadState,ActiveState,ControlGroup,Slice,KillMode';

export async function inspect_legacy_agent_unit(
  unit: string,
  stable: ReadonlyMap<number, WorkerProcess>,
): Promise<readonly WorkerProcess[]> {
  if (!UNIT.test(unit)) throw new Error('Invalid historical Panels service unit');
  const commands = new BackendCommands();
  const properties = async (): Promise<string> => {
    const result = await commands.run(['systemctl', '--user', 'show', unit, PROPERTIES], {
      timeout_seconds: 10,
    });
    if (result.exit_code !== 0) throw new Error('Historical service unit inspection failed');
    return result.standard_output;
  };
  try {
    const before = await properties();
    const values = Object.fromEntries(
      before
        .trim()
        .split('\n')
        .map((line) => {
          const separator = line.indexOf('=');
          return [line.slice(0, separator), line.slice(separator + 1)];
        }),
    );
    if (
      values.LoadState !== 'loaded' ||
      values.Slice !== 'panels-workers.slice' ||
      values.KillMode !== 'control-group'
    )
      throw new Error('Historical service unit ownership is not proven');
    const root = '/sys/fs/cgroup';
    const group = values.ControlGroup;
    if (!group || basename(group) !== unit)
      throw new Error('Historical service cgroup is not proven');
    const path = resolve(root, group.replace(/^\/+/, ''));
    if (!path_is_within(root, path) || (await realpath(path)) !== path)
      throw new Error('Historical service cgroup path is not proven');
    const identity = await stat(path, { bigint: true });
    const members = new Map<number, WorkerProcess>();
    const visit = async (directory: string): Promise<void> => {
      for (const text of (await readFile(join(directory, 'cgroup.procs'), 'utf8'))
        .split(/\s+/)
        .filter(Boolean)) {
        if (!/^\d+$/.test(text) || !Number.isSafeInteger(Number(text)))
          throw new Error('Historical service member is unreadable');
        const pid = Number(text);
        const observed = stable.get(pid);
        const current = parse_linux_worker_process(
          pid,
          await readFile(`/proc/${pid}/stat`, 'utf8'),
        );
        if (!observed || observed.birth !== current.birth)
          throw new Error('Historical service member changed during inspection');
        if (!current.zombie) members.set(pid, observed);
      }
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (entry.isSymbolicLink()) throw new Error('Historical service cgroup contains a link');
        if (entry.isDirectory()) await visit(join(directory, entry.name));
      }
    };
    await visit(path);
    if ((await properties()) !== before)
      throw new Error('Historical service unit changed during inspection');
    const after = await stat(path, { bigint: true });
    if (identity.dev !== after.dev || identity.ino !== after.ino)
      throw new Error('Historical service cgroup changed during inspection');
    return [...members.values()];
  } finally {
    await commands.shutdown();
  }
}
