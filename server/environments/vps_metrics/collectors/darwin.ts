/** Native Mac observations retain the shared sample, rate, and storage contracts. */
import { cpus, loadavg } from 'node:os';
import {
  darwin_process_bytes,
  darwin_process_ids,
  darwin_system_bytes,
  darwin_host_words,
  darwin_cpu_time_scale,
} from '../../../executor/mac/platform.ts';
import { page_size, statvfs, monotonic_seconds } from './platform.ts';
import type { Collector, Measurement, ProcessRecord, Sample } from '../contracts.ts';
import type { MetricsConfig } from '../configuration.ts';
import type { FilesystemStatistics } from './contracts.ts';
import { counter_rate, host_cpu_percent } from '../logic/rates.ts';
import { number } from '../codec.ts';
export interface DarwinMetricsReader {
  cpu_seconds_per_tick: number;
  boot_id(): string;
  cpu(): readonly number[];
  memory(): readonly [number, number];
  swap(): readonly [number, number];
  uptime(now: number): number;
  pids(): number[];
  bsd(pid: number): Buffer;
  task(pid: number): Buffer;
}
export interface DarwinMetricsDependencies {
  reader(): DarwinMetricsReader;
  wall_clock(): number;
  monotonic(): number;
  statvfs(path: string): FilesystemStatistics | Promise<FilesystemStatistics>;
  loadavg(): number[];
  cores(): number;
}
const NATIVE_UNAVAILABLE = 'No equivalent native observation is collected on macOS';
const measure = (
  value: number | null,
  unit: string,
  reason: string | null = null,
): Measurement => ({ value, unit, reason });
const reason = (error: unknown): string => {
  if (!(error instanceof Error)) throw error;
  return error.message;
};
function native_reader(): DarwinMetricsReader {
  const text = (name: string) =>
    darwin_system_bytes(name, 128, false).toString('ascii').replace(/\0.*$/s, '');
  return {
    cpu_seconds_per_tick: darwin_cpu_time_scale(),
    boot_id: () => {
      const boot = text('kern.bootsessionuuid');
      if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(boot))
        throw new Error('Native boot identity is unavailable');
      return boot.toLowerCase();
    },
    cpu: () => {
      const [user, system, idle, nice] = darwin_host_words(3, 4);
      return [user!, nice!, system!, idle!];
    },
    memory: () => {
      const total = Number(darwin_system_bytes('hw.memsize', 8).readBigUInt64LE());
      const [free, , inactive] = darwin_host_words(4, 4, true);
      return [total, (free! + inactive!) * page_size()];
    },
    swap: () => {
      const bytes = darwin_system_bytes('vm.swapusage', 32);
      return [Number(bytes.readBigUInt64LE(0)), Number(bytes.readBigUInt64LE(8))];
    },
    uptime: (now) => {
      const bytes = darwin_system_bytes('kern.boottime', 16);
      return now - Number(bytes.readBigInt64LE(0)) - bytes.readInt32LE(8) / 1e6;
    },
    pids: () => darwin_process_ids(8192, false),
    bsd: (pid) => darwin_process_bytes(pid, 3, 136),
    task: (pid) => darwin_process_bytes(pid, 4, 96),
  };
}
const native_dependencies: DarwinMetricsDependencies = {
  reader: native_reader,
  wall_clock: () => Date.now() / 1000,
  monotonic: monotonic_seconds,
  statvfs,
  loadavg,
  cores: () => cpus().length,
};
export class DarwinCollector implements Collector {
  #reader: DarwinMetricsReader | undefined;
  #boot = '';
  #cpu: readonly number[] | null = null;
  #time: number | null = null;
  #previous = new Map<string, number>();
  #slow: Record<string, Measurement> = {};
  readonly config: MetricsConfig;
  readonly dependencies: DarwinMetricsDependencies;
  constructor(
    config: MetricsConfig,
    dependencies: DarwinMetricsDependencies = native_dependencies,
  ) {
    this.config = config;
    this.dependencies = dependencies;
  }
  private birth(info: Buffer, pid: number): number {
    const sec = info.readBigUInt64LE(120),
      usec = info.readBigUInt64LE(128);
    const value = Number(sec * 1000000n + usec);
    if (info.readUInt32LE(12) !== pid || !sec || usec >= 1000000n || !Number.isSafeInteger(value))
      throw new Error('Native process identity is invalid');
    return value;
  }
  private processes(
    reader: DarwinMetricsReader,
    boot: string,
    same_boot: boolean,
    elapsed: number,
    deadline: number,
  ): {
    records: ProcessRecord[];
    count: number;
    reason: string | null;
  } {
    const counters = new Map<string, number>(),
      records: ProcessRecord[] = [];
    const finish = (count: number, error: string | null) => {
      this.#previous = counters;
      return { records, count, reason: error };
    };
    if (boot === 'unavailable') return finish(0, 'Native boot identity is unavailable');
    let pids: number[];
    try {
      pids = reader.pids();
      if (pids.length > 8192) throw new Error('Native process inventory exceeds its limit');
    } catch (error) {
      return finish(0, reason(error));
    }
    let failures = 0,
      error_reason: string | null = null;
    for (const pid of pids) {
      if (this.dependencies.monotonic() >= deadline) {
        error_reason = 'Native process inventory exceeded its time budget';
        break;
      }
      try {
        const info = reader.bsd(pid),
          birth = this.birth(info, pid);
        let task: Buffer | undefined,
          task_reason: string | null = null;
        try {
          task = reader.task(pid);
        } catch (error) {
          task_reason = reason(error);
        }
        if (this.birth(reader.bsd(pid), pid) !== birth)
          throw new Error('Native process identity changed during collection');
        const identity = `${pid}:${birth}`;
        let cpu = measure(null, 'percent_one_core', task_reason),
          rss = measure(null, 'bytes_rss', task_reason);
        if (task) {
          const counter = Number(task.readBigUInt64LE(16) + task.readBigUInt64LE(24));
          counters.set(identity, counter);
          cpu = counter_rate(counter, this.#previous.get(identity) ?? null, elapsed, {
            same_identity: same_boot,
            unit: 'percent_one_core',
            scale: reader.cpu_seconds_per_tick * 100,
          });
          rss = measure(Number(task.readBigUInt64LE(8)), 'bytes_rss');
        }
        if (task_reason) failures++;
        const text = (start: number, length: number) =>
          info
            .subarray(start, start + length)
            .toString('utf8')
            .replace(/\0.*$/s, '');
        records.push({
          identity: { boot_id: boot, pid, start_ticks: birth },
          parent_pid: info.readUInt32LE(16),
          uid: info.readUInt32LE(20),
          name: text(64, 32) || text(48, 16),
          state:
            (
              { 1: 'idle', 2: 'running', 3: 'sleeping', 4: 'stopped', 5: 'zombie' } as Record<
                number,
                string
              >
            )[info.readUInt32LE(4)] ?? 'unknown',
          service: 'unassigned',
          metrics: {
            cpu_percent: cpu,
            rss_bytes: rss,
            read_bytes_per_second: measure(null, 'bytes_per_second', NATIVE_UNAVAILABLE),
            write_bytes_per_second: measure(null, 'bytes_per_second', NATIVE_UNAVAILABLE),
          },
        });
      } catch (error) {
        failures++;
        error_reason = reason(error);
      }
    }
    if (failures)
      error_reason =
        `${failures} native process observations were unavailable. ${error_reason ?? ''}`.trim();
    return finish(pids.length, error_reason);
  }
  async collect(options: { include_slow: boolean }): Promise<Sample> {
    const d = this.dependencies,
      now = d.wall_clock(),
      mono = d.monotonic();
    this.#reader ??= d.reader();
    const reader = this.#reader;
    let boot: string;
    try {
      boot = reader.boot_id();
    } catch (error) {
      // Logged once when the boot identity becomes unavailable, not on every sample.
      if (this.#boot !== 'unavailable') console.warn('VPS metrics boot identity is unavailable', error);
      boot = 'unavailable';
    }
    const same_boot = boot !== 'unavailable' && boot === this.#boot,
      elapsed = this.#time === null ? 0 : mono - this.#time;
    const metrics: Record<string, Measurement> = {};
    for (const resource of ['cpu', 'memory', 'io'])
      for (const kind of ['some', 'full'])
        for (const period of ['avg10', 'avg60', 'avg300'])
          metrics[`pressure.${resource}.${kind}.${period}`] = measure(
            null,
            'percent',
            'Linux pressure observations are unavailable on macOS',
          );
    for (const [key, unit] of [
      ['cpu_percent', 'percent_total_capacity'],
      ['memory_total_bytes', 'bytes'],
      ['memory_available_bytes', 'bytes'],
      ['swap_total_bytes', 'bytes'],
      ['swap_free_bytes', 'bytes'],
      ['uptime_seconds', 'seconds'],
      ['temperature', 'celsius'],
      ['fan_speed', 'rpm'],
      ['disk.read_bytes_per_second', 'bytes_per_second'],
      ['disk.write_bytes_per_second', 'bytes_per_second'],
      ['network.receive_bytes_per_second', 'bytes_per_second'],
      ['network.transmit_bytes_per_second', 'bytes_per_second'],
    ])
      metrics[key!] = measure(null, unit!, NATIVE_UNAVAILABLE);
    try {
      const cpu = reader.cpu();
      metrics.cpu_percent = host_cpu_percent(cpu, this.#cpu, { same_identity: same_boot });
      if (metrics.cpu_percent.value !== null && elapsed > 0) {
        metrics.cpu_percent.numerator = number(metrics.cpu_percent.value) * elapsed;
        metrics.cpu_percent.duration = elapsed;
      }
      this.#cpu = cpu;
    } catch (error) {
      this.#cpu = null;
      metrics.cpu_percent = measure(null, 'percent_total_capacity', reason(error));
    }
    for (const [source, keys] of [
      [() => reader.memory(), ['memory_total_bytes', 'memory_available_bytes']],
      [() => reader.swap(), ['swap_total_bytes', 'swap_free_bytes']],
    ] as const) {
      try {
        const [total, available] = source();
        if (!(available >= 0 && available <= total))
          throw new Error('Native memory observation is inconsistent');
        metrics[keys[0]] = measure(total, 'bytes');
        metrics[keys[1]] = measure(available, 'bytes');
      } catch (error) {
        for (const key of keys) metrics[key] = measure(null, 'bytes', reason(error));
      }
    }
    try {
      const load = d.loadavg();
      for (const [index, period] of [1, 5, 15].entries())
        metrics[`load_${period}`] = measure(load[index]!, 'runnable_tasks');
    } catch (error) {
      for (const period of [1, 5, 15])
        metrics[`load_${period}`] = measure(null, 'runnable_tasks', reason(error));
    }
    try {
      const uptime = reader.uptime(now);
      if (!Number.isFinite(uptime) || uptime < 0) throw new Error('Native uptime is invalid');
      metrics.uptime_seconds = measure(uptime, 'seconds');
    } catch (error) {
      metrics.uptime_seconds = measure(null, 'seconds', reason(error));
    }
    if (options.include_slow) {
      try {
        const stats = await d.statvfs(this.config.filesystem_path);
        this.#slow = {
          'filesystem.total_bytes': measure(
            Number(stats.blocks) * Number(stats.fragment_size),
            'bytes',
          ),
          'filesystem.available_bytes': measure(
            Number(stats.available) * Number(stats.fragment_size),
            'bytes',
          ),
        };
      } catch (error) {
        this.#slow = Object.fromEntries(
          ['filesystem.total_bytes', 'filesystem.available_bytes'].map((key) => [
            key,
            measure(null, 'bytes', reason(error)),
          ]),
        );
      }
      for (const value of Object.values(this.#slow)) value.sampled_at = now;
    }
    Object.assign(metrics, this.#slow);
    const observed = this.processes(reader, boot, same_boot, elapsed, mono + 1);
    this.#boot = boot;
    this.#time = mono;
    return {
      timestamp: now,
      monotonic: mono,
      boot_id: boot,
      expected_interval: this.config.idle_seconds,
      core_count: d.cores(),
      metrics,
      services: [],
      processes: observed.records,
      process_count: observed.count,
      process_availability: observed.reason
        ? observed.records.length
          ? 'partial'
          : 'unavailable'
        : 'available',
      process_reason: observed.reason,
      disk_total_members: [],
      network_total_members: [],
    };
  }
}
