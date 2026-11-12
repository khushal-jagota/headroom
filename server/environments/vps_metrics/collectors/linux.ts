/** Direct proc, sysfs and cgroup evidence with source-local availability. */
import { basename, join } from 'node:path';
import type { MetricsConfig } from '../configuration.ts';
import type {
  Collector,
  Measurement,
  MetricNumber,
  ProcessRecord,
  Sample,
  ServiceRecord,
} from '../contracts.ts';
import { float, number } from '../codec.ts';
import { JsonNumber, stringify_python_json } from '../../../core/http/numeric_json.ts';
import { counter_rate, host_cpu_percent } from '../logic/rates.ts';
import type { CollectorDependencies, CollectorPaths } from './contracts.ts';
import { evidence_error, source_error } from './errors.ts';

type Metrics = Record<string, Measurement>;
function lines(value: string): string[] {
  if (!value) return [];
  const rows = value.split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/);
  if (rows.at(-1) === '') rows.pop();
  return rows;
}
const words = (value: string): string[] => value.split(/[\s\u001c-\u001f\u0085]+/u).filter(Boolean);
function decimal(value: string): string {
  return value
    .replace(/^[\s\u0085]+|[\s\u0085]+$/gu, '')
    .replace(/\p{Decimal_Number}/gu, (character) => {
      const point = character.codePointAt(0)!;
      let first = point;
      while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
      return String((point - first) % 10);
    });
}
const fail = (name: string): never => {
  throw evidence_error(name, 'invalid collection evidence');
};
function field<T>(values: readonly T[], index: number): T {
  if (index >= values.length) return fail('IndexError');
  return values[index]!;
}
function required(values: Record<string, number>, key: string): number {
  if (!(key in values)) return fail('KeyError');
  return values[key]!;
}
function integer(raw: string): MetricNumber {
  const clean = decimal(raw);
  if (!/^[+-]?\d(?:_?\d)*$/.test(clean)) return fail('ValueError');
  const token = BigInt(clean.replaceAll('_', '')).toString(),
    value = Number(token);
  return Number.isSafeInteger(value) ? value : new JsonNumber(token);
}
function nonnegative(raw: string): number {
  const clean = decimal(raw);
  if (
    !/^[+-]?(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:e[+-]?\d(?:_?\d)*)?$/i.test(
      clean,
    )
  )
    return fail('ValueError');
  const value = Number(clean.replaceAll('_', ''));
  if (!Number.isFinite(value) || value < 0) return fail('ValueError');
  return value;
}
function pairs(text: string): Record<string, number> {
  return Object.fromEntries(
    lines(text)
      .map(words)
      .filter((parts) => parts.length >= 2)
      .map((parts) => [parts[0]!.replace(/:+$/, ''), nonnegative(parts[1]!)]),
  );
}
function key_values(parts: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const part of parts) {
    const split = part.indexOf('=');
    if (split >= 0) result[part.slice(0, split)] = part.slice(split + 1);
  }
  return result;
}
function ordered(a: string, b: string): number {
  const left = [...a],
    right = [...b];
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const diff = left[i]!.codePointAt(0)! - right[i]!.codePointAt(0)!;
    if (diff) return diff;
  }
  return left.length - right.length;
}
function group_for(text: string): [string, string] {
  for (const line of lines(text))
    if (line.startsWith('0::')) {
      const path = line.slice(3),
        parts = path.split('/').filter((part) => part && part !== '.');
      for (const [suffix, kind] of [
        ['.service', 'service'],
        ['.scope', 'scope'],
      ])
        for (let i = parts.length - 1; i >= 0; i--)
          if (parts[i]!.endsWith(suffix!))
            return [(path.startsWith('/') ? '/' : '') + parts.slice(0, i + 1).join('/'), kind!];
    }
  return ['unassigned', 'unassigned'];
}
function measure(value: number | null, unit: string, reason: string | null = null): Measurement {
  return { value: value === null ? null : float(value), unit, reason };
}

export class LinuxCollector implements Collector {
  private previous = new Map<string, number>();
  private counters = new Map<string, number>();
  private cpu: number[] | null = null;
  private boot = '';
  private time: number | null = null;
  private elapsed = 0;
  private same_boot = false;
  private slow: Metrics = {};
  private states = new Map<string, [string, number | null]>();
  private slow_at = 0;
  private health_groups = new Map<string, string>();
  private state_errors = new Map<string, string>();
  private readonly config: MetricsConfig;
  private readonly paths: CollectorPaths;
  private readonly deps: CollectorDependencies;
  constructor(config: MetricsConfig, paths: CollectorPaths, dependencies: CollectorDependencies) {
    this.config = config;
    this.paths = paths;
    this.deps = dependencies;
  }
  private read(path: string): Promise<string> {
    return this.deps.read_text(path);
  }
  private async value(
    path: string,
    key: string,
    unit: string,
    metrics: Metrics,
    parser: (text: string) => number = nonnegative,
  ): Promise<void> {
    try {
      metrics[key] = measure(parser(await this.read(path)), unit);
    } catch (error) {
      metrics[key] = measure(
        null,
        unit,
        source_error(error, ['OSError', 'ValueError', 'IndexError', 'KeyError']),
      );
    }
  }
  private rate(key: string, value: number, unit: string, scale = 1): Measurement {
    this.counters.set(key, value);
    const previous = this.previous.get(key);
    return counter_rate(
      float(value),
      previous === undefined ? null : float(previous),
      float(this.elapsed),
      { same_identity: this.same_boot, unit, scale },
    );
  }
  private async pressure(root: string, prefix: string, metrics: Metrics): Promise<void> {
    for (const resource of ['cpu', 'memory', 'io']) {
      for (const kind of ['some', 'full'])
        for (const period of ['avg10', 'avg60', 'avg300'])
          metrics[`${prefix}${resource}.${kind}.${period}`] = measure(
            null,
            'percent',
            'pressure row unavailable',
          );
      const path = join(root, basename(root) === 'pressure' ? resource : resource + '.pressure');
      let text: string;
      try {
        text = await this.read(path);
      } catch (error) {
        const reason = source_error(error, ['OSError']);
        for (const kind of ['some', 'full'])
          for (const period of ['avg10', 'avg60', 'avg300'])
            metrics[`${prefix}${resource}.${kind}.${period}`] = measure(null, 'percent', reason);
        continue;
      }
      for (const row of lines(text)) {
        const parts = words(row);
        if (!['some', 'full'].includes(parts[0]!)) continue;
        const fields = key_values(parts.slice(1));
        for (const period of ['avg10', 'avg60', 'avg300']) {
          const key = `${prefix}${resource}.${parts[0]}.${period}`;
          try {
            if (!(period in fields)) fail('KeyError');
            metrics[key] = measure(nonnegative(fields[period]!), 'percent');
          } catch (error) {
            metrics[key] = measure(
              null,
              'percent',
              source_error(error, ['ValueError', 'KeyError']),
            );
          }
        }
      }
    }
  }
  private async host(): Promise<[Metrics, number, string[], string[]]> {
    const metrics: Metrics = {};
    let cores = 0;
    try {
      const rows = lines(await this.read(join(this.paths.proc_root, 'stat')));
      const cpu = words(field(rows, 0))
        .slice(1, 9)
        .map((value) => number(integer(value)));
      if (cpu.length < 4 || cpu.some((value) => value < 0)) fail('ValueError');
      cores = rows.filter((line) => /^cpu\d/.test(line)).length;
      metrics.cpu_percent = host_cpu_percent(cpu, this.cpu, { same_identity: this.same_boot });
      if (metrics.cpu_percent.value !== null && this.elapsed > 0) {
        metrics.cpu_percent.duration = float(this.elapsed);
        metrics.cpu_percent.numerator = float(number(metrics.cpu_percent.value) * this.elapsed);
      }
      this.cpu = cpu;
    } catch (error) {
      this.cpu = null;
      metrics.cpu_percent = measure(
        null,
        'percent_total_capacity',
        source_error(error, ['OSError', 'ValueError', 'IndexError']),
      );
    }
    const memory = [
      ['memory_total_bytes', 'MemTotal'],
      ['memory_available_bytes', 'MemAvailable'],
      ['swap_total_bytes', 'SwapTotal'],
      ['swap_free_bytes', 'SwapFree'],
    ] as const;
    try {
      const values = pairs(await this.read(join(this.paths.proc_root, 'meminfo')));
      for (const [name, source] of memory)
        metrics[name] = measure(
          source in values ? values[source]! * 1024 : null,
          'bytes',
          source in values ? null : 'missing field',
        );
    } catch (error) {
      const reason = source_error(error, ['OSError', 'ValueError']);
      for (const [name] of memory) metrics[name] = measure(null, 'bytes', reason);
    }
    for (const [index, period] of [1, 5, 15].entries())
      await this.value(
        join(this.paths.proc_root, 'loadavg'),
        `load_${period}`,
        'runnable_tasks',
        metrics,
        (text) => nonnegative(field(words(text), index)),
      );
    await this.value(
      join(this.paths.proc_root, 'uptime'),
      'uptime_seconds',
      'seconds',
      metrics,
      (text) => nonnegative(field(words(text), 0)),
    );
    await this.pressure(join(this.paths.proc_root, 'pressure'), 'pressure.', metrics);
    const disks = await this.disks(metrics),
      interfaces = await this.network(metrics);
    metrics.temperature = measure(null, 'celsius', 'unavailable on this guest');
    metrics.fan_speed = measure(null, 'rpm', 'unavailable on this guest');
    return [metrics, cores, disks, interfaces];
  }
  private async disks(metrics: Metrics): Promise<string[]> {
    let members: string[] = [];
    try {
      for (const name of await this.deps.directory(join(this.paths.sys_root, 'block')))
        if (
          !name.startsWith('loop') &&
          !name.startsWith('ram') &&
          (await this.deps.directory(join(this.paths.sys_root, 'block', name, 'slaves'))).length ===
            0
        )
          members.push(name);
      const counters = new Map<string, [number, number]>();
      for (const row of lines(await this.read(join(this.paths.proc_root, 'diskstats')))) {
        const fields = words(row);
        if (fields.length >= 14)
          counters.set(fields[2]!, [nonnegative(fields[5]!) * 512, nonnegative(fields[9]!) * 512]);
      }
      for (const [name, values] of counters)
        for (const [index, direction] of ['read', 'write'].entries())
          metrics[`disk.${name}.${direction}_bytes_per_second`] = this.rate(
            `disk.${name}.${direction}_bytes_per_second`,
            values[index]!,
            'bytes_per_second',
          );
      if (members.length === 0 || members.some((name) => !counters.has(name))) fail('ValueError');
      for (const [index, direction] of ['read', 'write'].entries()) {
        const key = `disk.${direction}_bytes_per_second`,
          identity = [...members].sort(ordered).join(',');
        metrics[key] = this.rate(
          key + ':' + identity,
          members.reduce((sum, name) => sum + counters.get(name)![index]!, 0),
          'bytes_per_second',
        );
      }
    } catch (error) {
      members = [];
      const reason = source_error(error, ['OSError', 'ValueError', 'IndexError']);
      for (const direction of ['read', 'write'])
        metrics[`disk.${direction}_bytes_per_second`] = measure(null, 'bytes_per_second', reason);
    }
    return members.sort(ordered);
  }
  private async network(metrics: Metrics): Promise<string[]> {
    const members: string[] = [];
    try {
      const totals = [0, 0];
      for (const row of lines(await this.read(join(this.paths.proc_root, 'net/dev'))).slice(2)) {
        const split = row.indexOf(':');
        if (split < 0) fail('ValueError');
        const name = row.slice(0, split).trim(),
          fields = words(row.slice(split + 1)),
          values = [nonnegative(field(fields, 0)), nonnegative(field(fields, 8))];
        for (const [index, direction] of ['receive', 'transmit'].entries())
          metrics[`network.${name}.${direction}_bytes_per_second`] = this.rate(
            `network.${name}.${direction}_bytes_per_second`,
            values[index]!,
            'bytes_per_second',
          );
        if (await this.deps.exists(join(this.paths.sys_root, 'class/net', name, 'device'))) {
          members.push(name);
          for (let i = 0; i < 2; i++) totals[i]! += values[i]!;
        }
      }
      if (members.length === 0) fail('ValueError');
      for (const [index, direction] of ['receive', 'transmit'].entries()) {
        const key = `network.${direction}_bytes_per_second`;
        metrics[key] = this.rate(
          key + ':' + [...members].sort(ordered).join(','),
          totals[index]!,
          'bytes_per_second',
        );
      }
    } catch (error) {
      const reason = source_error(error, ['OSError', 'ValueError', 'IndexError']);
      for (const direction of ['receive', 'transmit'])
        metrics[`network.${direction}_bytes_per_second`] = measure(
          null,
          'bytes_per_second',
          reason,
        );
    }
    return members.sort(ordered);
  }
  private async processes(
    boot: string,
  ): Promise<[ProcessRecord[], number, string[], Map<string, string>]> {
    const records: ProcessRecord[] = [],
      errors: string[] = [],
      groups = new Map<string, string>();
    let paths: string[];
    try {
      paths = (await this.deps.directory(this.paths.proc_root))
        .filter((name) => /^\p{Decimal_Number}+$/u.test(name))
        .sort((a, b) => Number(decimal(a)) - Number(decimal(b)));
    } catch (error) {
      return [[], 0, [source_error(error, ['OSError'])], groups];
    }
    const ticks = this.deps.clock_ticks(),
      pages = this.deps.page_size();
    for (const name of paths) {
      const path = join(this.paths.proc_root, name);
      try {
        const text = await this.read(join(path, 'stat')),
          start = text.indexOf('('),
          end = text.lastIndexOf(')');
        if (start < 0 || end < 0) fail('ValueError');
        const fields = words(text.slice(end + 2)),
          pid = integer(name),
          start_ticks = integer(field(fields, 19));
        const identity = `${boot}:${stringify_python_json(pid)}:${stringify_python_json(start_ticks)}`;
        const metrics: Metrics = {
          cpu_percent: this.rate(
            identity + ':cpu',
            nonnegative(field(fields, 11)) + nonnegative(field(fields, 12)),
            'percent_one_core',
            100 / ticks,
          ),
          rss_bytes: measure(nonnegative(field(fields, 21)) * pages, 'bytes_rss'),
        };
        let uid: MetricNumber | null = null;
        try {
          const row = lines(await this.read(join(path, 'status'))).find((line) =>
            line.startsWith('Uid:'),
          );
          if (row === undefined) fail('StopIteration');
          uid = integer(field(words(row!), 1));
        } catch (error) {
          source_error(error, ['OSError', 'ValueError', 'IndexError', 'StopIteration']);
          errors.push(`PID ${stringify_python_json(pid)} uid unavailable`);
        }
        let io: Record<string, number> = {},
          io_error: string | null = null;
        try {
          io = pairs(await this.read(join(path, 'io')));
        } catch (error) {
          io_error = source_error(error, ['OSError', 'ValueError']);
        }
        for (const direction of ['read', 'write']) {
          const key = direction + '_bytes';
          if (key in io)
            metrics[key + '_per_second'] = this.rate(
              identity + ':' + key,
              io[key]!,
              'bytes_per_second',
            );
          else {
            metrics[key + '_per_second'] = measure(
              null,
              'bytes_per_second',
              io_error ?? 'missing field',
            );
            errors.push(`PID ${stringify_python_json(pid)} ${direction} io unavailable`);
          }
        }
        let group: string, kind: string;
        try {
          [group, kind] = group_for(await this.read(join(path, 'cgroup')));
        } catch (error) {
          source_error(error, ['OSError']);
          group = kind = 'unassigned';
          errors.push(`PID ${stringify_python_json(pid)} cgroup unavailable`);
        }
        const final_stat = await this.read(join(path, 'stat')),
          final_end = final_stat.lastIndexOf(')');
        if (final_end < 0) fail('ValueError');
        if (
          stringify_python_json(integer(field(words(final_stat.slice(final_end + 2)), 19))) !==
          stringify_python_json(start_ticks)
        )
          fail('ValueError');
        groups.set(group, kind);
        records.push({
          identity: { boot_id: boot, pid, start_ticks },
          parent_pid: integer(field(fields, 1)),
          uid,
          name: text.slice(start + 1, end),
          state: field(fields, 0),
          service: group,
          metrics,
        });
      } catch (error) {
        source_error(error, ['OSError', 'ValueError', 'IndexError']);
        errors.push(`PID ${name} exited or stat unavailable`);
      }
    }
    return [records, paths.length, errors, groups];
  }
  private async collect_slow(): Promise<void> {
    this.slow_at = this.deps.wall_clock();
    for (const scope of ['system', 'user']) {
      this.state_errors.delete(scope);
      try {
        const text = await this.deps.service_query(
          scope,
          this.config.service_query_seconds,
          this.config.service_output_bytes,
        );
        this.states = new Map([...this.states].filter(([key]) => !key.startsWith(scope + ':')));
        this.health_groups = new Map(
          [...this.health_groups].filter(([, value]) => !value.startsWith(scope + ':')),
        );
        for (const block of text.split('\n\n')) {
          const values = key_values(lines(block));
          if (!('Id' in values)) continue;
          const health_key = scope + ':' + values.Id,
            group = values.ControlGroup || health_key;
          this.health_groups.set(group, health_key);
          this.states.set(health_key, [
            (values.ActiveState ?? '') + '/' + (values.SubState ?? ''),
            'NRestarts' in values ? nonnegative(values.NRestarts!) : null,
          ]);
        }
      } catch (error) {
        this.state_errors.set(
          scope,
          source_error(error, [
            'OSError',
            'ValueError',
            'SubprocessError',
            'CalledProcessError',
            'TimeoutExpired',
          ]),
        );
      }
    }
    try {
      const stats = await this.deps.statvfs(this.config.filesystem_path);
      this.slow = {
        'filesystem.total_bytes': measure(
          Number(BigInt(stats.blocks) * BigInt(stats.fragment_size)),
          'bytes',
        ),
        'filesystem.available_bytes': measure(
          Number(BigInt(stats.available) * BigInt(stats.fragment_size)),
          'bytes',
        ),
      };
    } catch (error) {
      const reason = source_error(error, ['OSError']);
      this.slow = {
        'filesystem.total_bytes': measure(null, 'bytes', reason),
        'filesystem.available_bytes': measure(null, 'bytes', reason),
      };
    }
    for (const metric of Object.values(this.slow)) metric.sampled_at = float(this.slow_at);
  }
  private async services(
    source: Map<string, string>,
    processes: ProcessRecord[],
  ): Promise<ServiceRecord[]> {
    const result: ServiceRecord[] = [],
      groups = new Map(source);
    for (const group of this.health_groups.keys())
      if (!groups.has(group)) groups.set(group, group.startsWith('/') ? 'service' : 'manager');
    for (const [group, kind] of [...groups].sort(([a], [b]) => ordered(a, b))) {
      const metrics: Metrics = {},
        path = join(this.paths.cgroup_root, group.replace(/^\//, ''));
      if (kind === 'unassigned' || kind === 'manager')
        for (const [key, unit] of [
          ['cpu_percent', 'percent_one_core'],
          ['memory_bytes', 'bytes_cgroup'],
          ['read_bytes_per_second', 'bytes_per_second'],
          ['write_bytes_per_second', 'bytes_per_second'],
        ])
          metrics[key!] = measure(null, unit!, 'no service or scope cgroup');
      else {
        try {
          const cpu = required(pairs(await this.read(join(path, 'cpu.stat'))), 'usage_usec'),
            identity = group + ':' + (await this.deps.inode(path));
          metrics.cpu_percent = this.rate(identity + ':cpu', cpu, 'percent_one_core', 0.0001);
        } catch (error) {
          metrics.cpu_percent = measure(
            null,
            'percent_one_core',
            source_error(error, ['OSError', 'ValueError', 'KeyError']),
          );
        }
        await this.value(join(path, 'memory.current'), 'memory_bytes', 'bytes_cgroup', metrics);
        await this.pressure(path, 'pressure.', metrics);
        try {
          const totals: Record<string, number> = { rbytes: 0, wbytes: 0 };
          for (const row of lines(await this.read(join(path, 'io.stat')))) {
            const values: Record<string, string> = {};
            for (const item of words(row).slice(1)) {
              const split = item.indexOf('=');
              if (split < 0) fail('ValueError');
              values[item.slice(0, split)] = item.slice(split + 1);
            }
            for (const key of Object.keys(totals)) {
              if (!(key in values)) fail('KeyError');
              totals[key]! += nonnegative(values[key]!);
            }
          }
          for (const [direction, key] of [
            ['read', 'rbytes'],
            ['write', 'wbytes'],
          ])
            metrics[direction + '_bytes_per_second'] = this.rate(
              `${group}:${await this.deps.inode(path)}:${key}`,
              totals[key!]!,
              'bytes_per_second',
            );
        } catch (error) {
          const reason = source_error(error, ['OSError', 'ValueError', 'KeyError']);
          for (const direction of ['read', 'write'])
            metrics[direction + '_bytes_per_second'] = measure(null, 'bytes_per_second', reason);
        }
      }
      const health_key = this.health_groups.get(group),
        scope = health_key
          ? health_key.split(':', 1)[0]!
          : group.includes('/user.slice/')
            ? 'user'
            : 'system';
      const health = this.states.get(health_key ?? scope + ':' + basename(group)),
        state_error = this.state_errors.get(scope);
      metrics.restarts = measure(
        health && !state_error ? health[1] : null,
        'count',
        state_error ?? (health ? null : 'service state unavailable'),
      );
      metrics.restarts.sampled_at = float(this.slow_at);
      if (health && health[1] === null) metrics.restarts.reason = 'restart count unavailable';
      result.push({
        identity: group,
        name: health_key ? health_key.slice(health_key.indexOf(':') + 1) : basename(group),
        kind,
        metrics,
        state: health && !state_error ? health[0] : null,
        state_reason: state_error ?? (health ? null : 'service state unavailable'),
        process_count: processes.filter((process) => process.service === group).length,
      });
    }
    return result;
  }
  async collect(options: { include_slow: boolean }): Promise<Sample> {
    const now = this.deps.wall_clock(),
      mono = this.deps.monotonic();
    let boot: string;
    try {
      boot = (await this.read(join(this.paths.proc_root, 'sys/kernel/random/boot_id'))).trim();
      if (!boot) fail('ValueError');
    } catch (error) {
      source_error(error, ['OSError', 'ValueError']);
      boot = 'unavailable';
    }
    this.same_boot = boot !== 'unavailable' && boot === this.boot;
    this.elapsed = this.time === null ? 0 : mono - this.time;
    this.counters = new Map();
    const [metrics, cores, disks, interfaces] = await this.host(),
      [processes, count, errors, groups] = await this.processes(boot);
    if (options.include_slow) await this.collect_slow();
    Object.assign(metrics, this.slow);
    const services = await this.services(groups, processes);
    this.previous = this.counters;
    this.boot = boot;
    this.time = mono;
    return {
      timestamp: float(now),
      monotonic: float(mono),
      boot_id: boot,
      expected_interval: float(this.config.idle_seconds),
      core_count: cores,
      metrics,
      services,
      processes,
      process_count: count,
      process_availability: errors.length
        ? processes.length
          ? 'partial'
          : 'unavailable'
        : 'available',
      process_reason: errors.length ? errors.join('; ') : null,
      disk_total_members: disks,
      network_total_members: interfaces,
    };
  }
}
