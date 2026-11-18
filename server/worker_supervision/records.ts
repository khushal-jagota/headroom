import {
  JsonNumber,
  numeric_kind,
  numeric_value,
  parse_numeric_json,
  stringify_python_json,
} from '../core/http/numeric_json.ts';
/** Private journals survive child exit, parent restart, and disposable scratch removal. */
import { constants } from 'node:fs';
import { lstat, open, readFile, rename, unlink } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as pause } from 'node:timers/promises';
import { flock } from '../core/locks/flock.ts';
import type { WorkerProcessRecord } from './contracts.ts';
import { process_from_json } from './logic/ownership.ts';

export async function read(path: string): Promise<WorkerProcessRecord> {
  if (!/^[0-9a-f]{32}\.json$/.test(basename(path)))
    throw new Error('Invalid Worker process record name');
  for (const candidate of [dirname(path), path]) {
    const info = await lstat(candidate);
    if (info.uid !== process.getuid!() || (info.mode & 0o077) !== 0 || info.isSymbolicLink())
      throw new Error('Worker process record is not private and owned');
  }
  const value: unknown = parse_numeric_json(await readFile(path, 'utf8'));
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !('version' in value) ||
    numeric_kind(value.version) !== 'integer' ||
    numeric_value(value.version) !== 1
  )
    throw new Error('Invalid Worker process record');
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.processes) || typeof record.ticket_id !== 'string')
    throw new Error('Invalid Worker process ownership');
  // Validate process authority before a caller can use any recorded incarnation.
  const processes = record.processes.map(process_from_json);
  const owner = record.owner == null ? record.owner : process_from_json(record.owner);
  const root = record.root == null ? record.root : process_from_json(record.root);
  let cgroup: WorkerProcessRecord['cgroup'];
  if (record.cgroup !== undefined) {
    const raw = record.cgroup;
    if (
      raw === null ||
      typeof raw !== 'object' ||
      Array.isArray(raw) ||
      !('path' in raw) ||
      typeof raw.path !== 'string'
    )
      throw new Error('Invalid Worker cgroup identity');
    const values = raw as Record<string, unknown>;
    const exact = (key: string): bigint => {
      const value = values[key];
      if (
        !(value instanceof JsonNumber) ||
        value.kind !== 'integer' ||
        value.integer === null ||
        value.integer < 0n
      )
        throw new Error('Invalid Worker cgroup identity');
      return value.integer;
    };
    cgroup = {
      path: raw.path,
      inode: exact('inode'),
      root_device: exact('root_device'),
      root_inode: exact('root_inode'),
    };
    if (cgroup.inode <= 0n) throw new Error('Invalid Worker cgroup identity');
  }
  const members = record.unattributed_session_members;
  if (record.launchd !== undefined) {
    const job = record.launchd;
    if (
      job === null ||
      typeof job !== 'object' ||
      Array.isArray(job) ||
      !('domain' in job) ||
      typeof job.domain !== 'string' ||
      !('label' in job) ||
      typeof job.label !== 'string'
    )
      throw new Error('Invalid Worker launchd identity');
  }
  if (members !== undefined && !Array.isArray(members))
    throw new Error('Invalid Worker session evidence');
  for (const key of [
    'host',
    'boot',
    'state',
    'launch_phase',
    'reason',
    'cgroup_error',
    'observation_error',
    'launchd_socket_directory',
  ])
    if (record[key] !== undefined && typeof record[key] !== 'string')
      throw new Error(`Invalid Worker record ${key}`);
  for (const key of ['conversation_id', 'source_child_id', 'source_incarnation'])
    if (record[key] != null && typeof record[key] !== 'string')
      throw new Error(`Invalid Worker record ${key}`);
  for (const key of [
    'isolated',
    'launch_revoked',
    'boot_ended',
    'launch_failed',
    'cgroup_joined',
    'worker_priority',
  ])
    if (record[key] !== undefined && typeof record[key] !== 'boolean')
      throw new Error(`Invalid Worker record ${key}`);
  return {
    ...record,
    version: 1,
    ticket_id: record.ticket_id,
    processes,
    ...(owner === undefined ? {} : { owner }),
    ...(root === undefined ? {} : { root }),
    ...(cgroup === undefined ? {} : { cgroup }),
    ...(members === undefined
      ? {}
      : { unattributed_session_members: (members as unknown[]).map(process_from_json) }),
  } as WorkerProcessRecord;
}

export async function write(path: string, record: WorkerProcessRecord): Promise<void> {
  const temporary = join(dirname(path), `.${basename(path)}.${randomUUID().replaceAll('-', '')}`);
  let created = false;
  try {
    const file = await open(
      temporary,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
      0o600,
    );
    created = true;
    try {
      await file.writeFile(
        stringify_python_json(record, { sort_keys: true, ensure_ascii: true }) + '\n',
        'utf8',
      );
      await file.sync();
    } finally {
      await file.close();
    }
    await rename(temporary, path);
    const parent = await open(dirname(path), constants.O_RDONLY);
    try {
      await parent.sync();
    } finally {
      await parent.close();
    }
  } finally {
    if (created)
      await unlink(temporary).catch((error: NodeJS.ErrnoException) => {
        if (error.code !== 'ENOENT') throw error;
      });
  }
}

const lock_path = (path: string): string => path.slice(0, -5) + '.lock';

/** Removes a record and its lock. The caller holds the record lock. */
export async function remove(path: string): Promise<void> {
  for (const file of [path, lock_path(path)])
    await unlink(file).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== 'ENOENT') throw error;
    });
}

export async function locked<T>(
  path: string,
  deadline: number,
  operation: () => Promise<T>,
  options: {
    clock?: () => number;
    wait?: boolean;
  } = {},
): Promise<T> {
  const clock = options.clock ?? (() => performance.now() / 1000);
  const file = await open(
    lock_path(path),
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW,
    0o600,
  );
  let held = false;
  try {
    const info = await file.stat();
    if (info.uid !== process.getuid!() || (info.mode & 0o077) !== 0 || !info.isFile())
      throw new Error('Worker process lock is not private and owned');
    for (;;) {
      if (clock() >= deadline)
        throw new DOMException('Worker process journal lock deadline expired', 'TimeoutError');
      const result = flock(file.fd, 'try-exclusive');
      if (result === 0) {
        held = true;
        break;
      }
      if (result !== 11 && result !== 35)
        throw new Error(`Worker journal lock failed: errno ${result}`);
      if (options.wait === false)
        throw new DOMException('Worker process journal is busy', 'TimeoutError');
      await pause(Math.min(10, Math.max(0, deadline - clock()) * 1000));
    }
    return await operation();
  } finally {
    try {
      if (held) flock(file.fd, 'unlock');
    } finally {
      await file.close();
    }
  }
}
