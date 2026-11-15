/** Export-only comparison of Python's existing populated native fixtures. */
import { DarwinProcessInspector } from './darwin.ts';
import { require_eligible } from './logic/eligibility.ts';
import type { ProcessIdentity } from './contracts.ts';
import { DarwinCollector } from '../environments/vps_metrics/collectors/darwin.ts';
import { DEFAULT_METRICS_CONFIG } from '../environments/vps_metrics/configuration.ts';
import { encode } from '../environments/vps_metrics/codec.ts';
interface Action {
  now: number;
  boot: string;
  cpu: number[];
  pids: number[];
  bsd: Record<string, string[]>;
  task: Record<string, string | null>;
  preview: Record<string, { info: string; paths: string; sockets: string[] }>;
  operation: 'discover' | 'check' | 'connected';
  unreadable?: number[];
  truncated?: boolean;
  socket_error?: boolean;
  failure_status?: boolean;
}
export async function native_parity(input: {
  worktree: string;
  actions: Action[];
}): Promise<string> {
  let action = input.actions[0]!;
  const reads = new Map<number, number>();
  const bytes = (raw: string) => Buffer.from(raw, 'base64');
  const collector = new DarwinCollector(DEFAULT_METRICS_CONFIG, {
    reader: () => ({
      cpu_seconds_per_tick: 1 / 24000000,
      boot_id: () => {
        if (!action.boot) throw new Error('boot UUID unavailable');
        return action.boot;
      },
      cpu: () => action.cpu,
      memory: () => [1000, 250],
      swap: () => [500, 100],
      uptime: () => 100,
      pids: () => action.pids,
      bsd: (pid) => {
        const index = reads.get(pid) ?? 0;
        reads.set(pid, index + 1);
        const values = action.bsd[pid];
        if (!values?.length) throw new Error('BSD info denied');
        return bytes(values[Math.min(index, values.length - 1)]!);
      },
      task: (pid) => {
        const value = action.task[pid];
        if (!value) throw new Error('task info denied');
        return bytes(value);
      },
    }),
    wall_clock: () => action.now,
    monotonic: () => action.now,
    statvfs: () => ({ blocks: 100, available: 25, fragment_size: 4096 }),
    loadavg: () => [1, 2, 3],
    cores: () => 4,
  });
  const preview = new DarwinProcessInspector({
    boot: () => 'boot',
    pids: () => {
      if (action.truncated) throw new Error('Process discovery bound exceeded.');
      return Object.keys(action.preview).map(Number);
    },
    info: (pid, flavor) =>
      bytes(flavor === 3 ? action.preview[pid]!.info : action.preview[pid]!.paths),
    descriptors: (pid) => {
      if (action.unreadable?.includes(pid))
        throw Object.assign(new Error('Descriptors denied'), { code: 'EPERM' });
      return action.preview[pid]!.sockets.map((_, index) => index);
    },
    socket: (pid, descriptor) => {
      if (action.socket_error) throw Object.assign(new Error('Socket denied'), { code: 'EPERM' });
      return bytes(action.preview[pid]!.sockets[descriptor]!);
    },
  });
  let identity: ProcessIdentity | undefined;
  const results: unknown[] = [];
  for (const current of input.actions) {
    action = current;
    reads.clear();
    let evidence: unknown;
    try {
      if (action.operation === 'discover') identity = await preview.discover(3001, input.worktree);
      else if (action.operation === 'connected') await preview.connected(identity!, 45678);
      else await preview.check(identity!);
      evidence = action.operation === 'discover' ? identity : 'accepted';
    } catch (error) {
      evidence = action.failure_status
        ? { status_code: (error as { status_code: number }).status_code }
        : (error as Error).message;
    }
    const sample = await collector.collect({ include_slow: results.length === 0 });
    results.push({ preview: evidence, sample });
  }
  require_eligible({
    ticket_id: 'home',
    machine_id: (await import('../execution_identity.ts')).HOME_MACHINE_ID,
    terminal: false,
    project_folder: input.worktree,
  });
  return encode(results);
}
