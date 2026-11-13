/** Async, readonly kernel evidence. No process commands or event-loop blocking scans. */
import { opendir, readFile, readlink, stat } from 'node:fs/promises';
import { endianness } from 'node:os';
import { join } from 'node:path';
import { testModeFromEnvironment } from '../config.ts';
import { is_os_error } from '../system_errors.ts';
import { InvalidPeerEvidence } from './contracts.ts';
import type { Endpoint, PeerLookupContext, PeerLookupResult, ProcReader } from './contracts.ts';
import { address_bytes, agent_cgroup, integer, proc_address, splitlines, words } from './logic.ts';

function disappeared(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
export const proc_reader: ProcReader = {
  async readText(path) {
    const bytes = await readFile(path);
    try {
      return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
    } catch {
      throw new InvalidPeerEvidence('invalid proc text');
    }
  },
  async list(path) {
    const names: string[] = [];
    // readdir sorts names. opendir preserves the source's kernel enumeration order.
    for await (const entry of await opendir(path)) names.push(entry.name);
    return names;
  },
  async uid(path) {
    return (await stat(path, { bigint: true })).uid;
  },
  readlink,
};
const default_context = (): PeerLookupContext => ({
  proc_root: '/proc',
  current_uid: BigInt(process.getuid?.() ?? -1),
  little_endian: endianness() === 'LE',
  files: proc_reader,
});
function field(fields: readonly string[], index: number): string {
  if (fields[index] === undefined) throw new InvalidPeerEvidence('missing socket field');
  return fields[index];
}
function port_of(encoded: string): string {
  const parts = encoded.split(':');
  if (parts.length !== 2) throw new InvalidPeerEvidence('invalid socket endpoint');
  return parts[1];
}
function equals(left: readonly number[], right: readonly number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
export async function resolve_loopback_peer(
  client: Endpoint | null,
  server: Endpoint | null,
  context?: PeerLookupContext,
): Promise<PeerLookupResult> {
  if (
    context === undefined &&
    (process.env.VITEST === 'true' ||
      process.env.PYTEST_CURRENT_TEST !== undefined ||
      testModeFromEnvironment())
  )
    return { is_owner: false, skipped_unreadable_processes: 0 };
  context ??= default_context();
  let skipped = 0;
  const result = (is_owner: boolean): PeerLookupResult => ({
    is_owner,
    skipped_unreadable_processes: skipped,
  });
  const { files, proc_root, current_uid, little_endian } = context;
  try {
    if (
      client === null ||
      server === null ||
      !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(client[0])
    )
      return result(false);
    const peer = address_bytes(client[0])!;
    const sockets = new Map<string, readonly [bigint, bigint]>();
    for (const table of ['tcp', 'tcp6'])
      for (const line of splitlines(await files.readText(join(proc_root, 'net', table))).slice(1)) {
        const fields = words(line),
          local = field(fields, 1);
        const local_host = local.split(':')[0];
        const local_port = port_of(local),
          remote_port = port_of(field(fields, 2));
        if (
          field(fields, 3) === '01' &&
          integer(local_port, 16) === BigInt(client[1]) &&
          integer(remote_port, 16) === BigInt(server[1]) &&
          equals(proc_address(local_host, little_endian), peer)
        ) {
          const inode = integer(field(fields, 9)),
            uid = integer(field(fields, 7));
          sockets.set(`${inode}:${uid}`, [inode, uid]);
        }
      }
    if (sockets.size !== 1) return result(false);
    const [inode, uid] = sockets.values().next().value!;
    if (uid !== current_uid || inode <= 0n) return result(false);
    const target = `socket:[${inode}]`;
    let holders = 0;
    for (const process of await files.list(proc_root)) {
      if (!/^\p{Decimal_Number}+$/u.test(process)) continue;
      const path = join(proc_root, process);
      try {
        if ((await files.uid(path)) !== uid) continue;
      } catch (error) {
        if (disappeared(error)) continue;
        throw error;
      }
      let descriptors: readonly string[];
      try {
        descriptors = await files.list(join(path, 'fd'));
      } catch (error) {
        if (disappeared(error)) continue;
        if (!is_os_error(error)) throw error;
        if (agent_cgroup(await files.readText(join(path, 'cgroup')))) return result(false);
        skipped++;
        continue;
      }
      let process_holders = 0,
        complete = true;
      for (const descriptor of descriptors) {
        const fd = join(path, 'fd', descriptor);
        let link: string;
        try {
          link = await files.readlink(fd);
        } catch (error) {
          if (disappeared(error)) continue;
          if (!is_os_error(error)) throw error;
          if (agent_cgroup(await files.readText(join(path, 'cgroup')))) return result(false);
          skipped++;
          complete = false;
          break;
        }
        if (link !== target) continue;
        const cgroup = await files.readText(join(path, 'cgroup'));
        if ((await files.readlink(fd)) !== target || agent_cgroup(cgroup)) return result(false);
        process_holders++;
      }
      if (complete) holders += process_holders;
    }
    return result(holders > 0);
  } catch (error) {
    if (!is_os_error(error) && !(error instanceof InvalidPeerEvidence)) throw error;
    return result(false);
  }
}
