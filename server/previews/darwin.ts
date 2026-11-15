/** Bounded libproc evidence. No command text or PID-only ownership enters admission. */
import { realpath } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { path_is_within } from '../core/path_containment.ts';
import {
  darwin_process_bytes,
  darwin_process_ids,
  darwin_process_descriptors,
  darwin_process_socket,
  darwin_system_bytes,
} from '../executor/mac/platform.ts';
import { PreviewError, type ProcessIdentity, type ProcessInspector } from './contracts.ts';
import * as config from './config.ts';
export interface DarwinPreviewKernel {
  boot(): string;
  pids(limit: number): number[];
  info(pid: number, flavor: number, size: number): Buffer;
  descriptors(pid: number, limit: number): number[];
  socket(pid: number, descriptor: number): Buffer | null;
}
const kernel: DarwinPreviewKernel = {
  boot: () =>
    darwin_system_bytes('kern.bootsessionuuid', 128, false)
      .toString('ascii')
      .replace(/\0.*$/s, '')
      .toLowerCase(),
  pids: (limit) => darwin_process_ids(limit, true),
  info: darwin_process_bytes,
  descriptors: darwin_process_descriptors,
  socket: darwin_process_socket,
};
interface SocketEvidence {
  generation: bigint;
  local_port: number;
  remote_port: number;
  loopback: boolean;
  remote_loopback: boolean;
  state: number;
}
export class DarwinProcessInspector implements ProcessInspector {
  #boot: string | undefined;
  readonly native: DarwinPreviewKernel;
  readonly clock: () => number;
  constructor(native: DarwinPreviewKernel = kernel, clock = () => performance.now() / 1000) {
    this.native = native;
    this.clock = clock;
  }
  private observe<T>(call: () => T): T {
    try {
      return call();
    } catch (cause) {
      if (cause instanceof PreviewError) throw cause;
      const message = cause instanceof Error ? cause.message : String(cause);
      const status = /bound|limit|incomplete/.test(message) ? 503 : 410;
      throw Object.assign(new PreviewError(message, status), {
        cause,
        code: (cause as NodeJS.ErrnoException).code,
      });
    }
  }
  private deadline(deadline: number): void {
    if (this.clock() >= deadline)
      throw new PreviewError('Process inspection exceeded its deadline.', 503);
  }
  private sockets(pid: number, deadline: number, budget: { count: number }): SocketEvidence[] {
    this.deadline(deadline);
    const remaining = config.DISCOVERY_FDS - budget.count;
    if (remaining <= 0)
      throw new PreviewError('Process descriptor inspection bound exceeded.', 503);
    const descriptors = this.observe(() => this.native.descriptors(pid, remaining));
    if (descriptors.length > remaining)
      throw new PreviewError('Process descriptor inspection bound exceeded.', 503);
    budget.count += descriptors.length;
    const result: SocketEvidence[] = [];
    for (const descriptor of descriptors) {
      this.deadline(deadline);
      if (descriptor < 0) continue;
      const bytes = this.observe(() => this.native.socket(pid, descriptor));
      this.deadline(deadline);
      if (!bytes) continue;
      // XNU socket_fdinfo: socket at 24, protocol union at 264, in_sockinfo first.
      if (bytes.readInt32LE(256) !== 2 || bytes.readInt32LE(180) !== 6) continue;
      const ipv4 = bytes.readInt32LE(184) === 2;
      result.push({
        generation: bytes.readBigUInt64LE(272),
        local_port: bytes.readUInt16BE(268),
        remote_port: bytes.readUInt16BE(264),
        loopback: ipv4 && bytes.subarray(324, 328).equals(Buffer.from([127, 0, 0, 1])),
        remote_loopback: ipv4 && bytes.subarray(308, 312).equals(Buffer.from([127, 0, 0, 1])),
        state: bytes.readInt32LE(344),
      });
    }
    return result;
  }
  private async identity(
    pid: number,
    generation: bigint,
    port: number,
    worktree: string,
    deadline: number,
  ): Promise<ProcessIdentity> {
    this.deadline(deadline);
    const info = this.observe(() => this.native.info(pid, 3, 136)),
      paths = this.observe(() => this.native.info(pid, 9, 2352));
    const raw = paths.subarray(152, 1176),
      end = raw.indexOf(0);
    const seconds = info.readBigUInt64LE(120),
      micros = info.readBigUInt64LE(128);
    if (
      info.readUInt32LE(12) !== pid ||
      info.readUInt32LE(20) !== process.getuid!() ||
      seconds === 0n ||
      micros >= 1000000n ||
      end < 1
    )
      throw new PreviewError(
        'Listener cwd or ownership is outside the Ticket preview worktree.',
        410,
      );
    const cwd = await realpath(raw.subarray(0, end).toString()).catch(() => {
      throw new PreviewError(
        'Listener cwd or ownership is outside the Ticket preview worktree.',
        410,
      );
    });
    if (!path_is_within(worktree, cwd))
      throw new PreviewError(
        'Listener cwd or ownership is outside the Ticket preview worktree.',
        410,
      );
    const birth = Number(seconds * 1000000n + micros);
    if (!Number.isSafeInteger(birth))
      throw new PreviewError('Mac process birth exceeds exact storage.', 410);
    this.#boot ??= this.observe(() => this.native.boot());
    if (this.native === kernel && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(this.#boot))
      throw new PreviewError('Mac boot evidence is unavailable.', 410);
    if (!this.#boot) throw new PreviewError('Mac boot evidence is unavailable.', 410);
    this.deadline(deadline);
    return {
      boot_id: this.#boot,
      pid,
      start_ticks: birth,
      listener_inode:
        generation <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(generation) : generation,
      port,
      worktree,
    };
  }
  async discover(port: number, worktree: string): Promise<ProcessIdentity> {
    const deadline = this.clock() + config.INSPECTION_SECONDS;
    const owners = new Map<string, { pid: number; socket: SocketEvidence }>();
    const budget = { count: 0 },
      pids = this.observe(() => this.native.pids(config.DISCOVERY_PIDS));
    if (pids.length > config.DISCOVERY_PIDS)
      throw new PreviewError('Process discovery bound exceeded.', 503);
    for (const pid of pids) {
      let sockets: SocketEvidence[];
      try {
        sockets = this.sockets(pid, deadline, budget);
      } catch (error) {
        if (['EACCES', 'EPERM'].includes((error as NodeJS.ErrnoException).code ?? '')) continue;
        throw error;
      }
      for (const socket of sockets)
        if (socket.state === 1 && socket.local_port === port)
          owners.set(`${pid}:${socket.generation}`, { pid, socket });
    }
    if (owners.size !== 1) throw new PreviewError('Listener ownership is missing or shared.');
    const { pid, socket } = [...owners.values()][0]!;
    if (!socket.loopback || socket.generation <= 0n || socket.generation >= 2n ** 63n)
      throw new PreviewError('Require exactly one listener bound to 127.0.0.1.');
    const identity = await this.identity(pid, socket.generation, port, worktree, deadline);
    await this.check(identity, deadline);
    return identity;
  }
  async check(
    identity: ProcessIdentity,
    deadline = this.clock() + config.INSPECTION_SECONDS,
  ): Promise<void> {
    const current = () =>
      this.identity(
        identity.pid,
        BigInt(identity.listener_inode),
        identity.port,
        identity.worktree,
        deadline,
      );
    const before = await current(),
      sockets = this.sockets(identity.pid, deadline, { count: 0 });
    const after = await current();
    if (
      [before, after].some(
        (value) => value.boot_id !== identity.boot_id || value.start_ticks !== identity.start_ticks,
      )
    )
      throw new PreviewError('Preview process identity changed.', 410);
    const listeners = sockets.filter(
      (value) => value.state === 1 && value.local_port === identity.port,
    );
    if (
      !listeners.length ||
      listeners.some(
        (value) => !value.loopback || value.generation !== BigInt(identity.listener_inode),
      )
    )
      throw new PreviewError('Preview listener ended or changed.', 410);
  }
  async connected(identity: ProcessIdentity, local_port: number): Promise<void> {
    const deadline = this.clock() + config.INSPECTION_SECONDS;
    for (;;) {
      await this.check(identity, deadline);
      if (
        this.sockets(identity.pid, deadline, { count: 0 }).some(
          (value) =>
            value.loopback &&
            value.remote_loopback &&
            value.state === 4 &&
            value.local_port === identity.port &&
            value.remote_port === local_port,
        )
      ) {
        await this.check(identity, deadline);
        return;
      }
      this.deadline(deadline);
      await delay(
        Math.min(config.ACCEPT_RETRY_SECONDS * 1000, Math.max(0, (deadline - this.clock()) * 1000)),
      );
    }
  }
}
