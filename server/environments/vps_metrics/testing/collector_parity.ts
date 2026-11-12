/** Export-only proofs use synthetic trees, statistics, clocks and exact scripted children. */
import { mkdir, writeFile, unlink, rename, cp } from 'node:fs/promises';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { setTimeout as pause } from 'node:timers/promises';
import type { ChildProcess } from 'node:child_process';
import { LinuxCollector } from '../collectors/linux.ts';
import { filesystem_evidence } from '../collectors/index.ts';
import { query_services } from '../collectors/systemd.ts';
import { evidence_error } from '../collectors/errors.ts';
import { metrics_config } from '../configuration.ts';
import { encode } from '../codec.ts';
interface Reply {
  error?: string;
  text?: string;
}
interface Action {
  now: number;
  include_slow: boolean;
  write?: Record<string, string>;
  remove?: string[];
  mkdir?: string[];
  clone_group?: string;
  service?: Record<string, Reply>;
  filesystem_error?: string | null;
  reuse?: { path: string; replacement: string };
}
interface Probe {
  root: string;
  service: Record<string, Reply>;
  actions: Action[];
}
function inside(root: string, path: string): string {
  const target = resolve(root, path),
    suffix = relative(root, target);
  if (suffix === '..' || suffix.startsWith('../') || isAbsolute(suffix))
    throw new Error('collector probe escaped its synthetic root');
  return target;
}
export async function collector_parity(request: Probe): Promise<string> {
  const fs = filesystem_evidence(),
    calls: string[] = [],
    samples: unknown[] = [];
  let clock = 100,
    service = request.service,
    filesystem_error: string | null = null,
    stat_calls = 0,
    reuse: Action['reuse'],
    reads = 0;
  const collector = new LinuxCollector(
    metrics_config(),
    {
      proc_root: join(request.root, 'proc'),
      sys_root: join(request.root, 'sys'),
      cgroup_root: join(request.root, 'cgroup'),
    },
    {
      ...fs,
      read_text: async (path) => {
        if (['cmdline', 'environ'].includes(path.split('/').at(-1)!))
          throw new Error('sensitive process field read');
        inside(request.root, path);
        if (reuse && path === inside(request.root, reuse.path) && ++reads === 2)
          await writeFile(path, reuse.replacement);
        return fs.read_text(path);
      },
      wall_clock: () => clock,
      monotonic: () => clock,
      clock_ticks: () => 100,
      page_size: () => 4096,
      statvfs: () => {
        stat_calls++;
        if (filesystem_error)
          throw evidence_error(filesystem_error, 'scripted filesystem unavailable');
        return { blocks: 1000, available: 250, fragment_size: 4096 };
      },
      service_query: async (scope, timeout, output) => {
        if (timeout !== 1 || output !== 1024 * 1024)
          throw new Error('service command bounds changed');
        calls.push(scope);
        const reply = service[scope]!;
        if (reply.error) throw evidence_error(reply.error, 'scripted service unavailable');
        return reply.text!;
      },
    },
  );
  for (const action of request.actions) {
    clock = action.now;
    if (action.service) service = action.service;
    if ('filesystem_error' in action) filesystem_error = action.filesystem_error ?? null;
    reuse = action.reuse;
    reads = 0;
    for (const path of action.mkdir ?? [])
      await mkdir(inside(request.root, path), { recursive: true });
    for (const [path, text] of Object.entries(action.write ?? {})) {
      const target = inside(request.root, path);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, text);
    }
    for (const path of action.remove ?? []) await unlink(inside(request.root, path));
    if (action.clone_group) {
      const path = inside(request.root, action.clone_group);
      await rename(path, path + '.old');
      await cp(path + '.old', path, { recursive: true });
    }
    samples.push(await collector.collect({ include_slow: action.include_slow }));
  }
  return encode({ samples, calls, stat_calls });
}

interface CommandProbe {
  script: string;
  cases: { mode: string; timeout: number; limit: number; completion?: string }[];
}
export async function collector_commands(request: CommandProbe): Promise<string> {
  const results: unknown[] = [];
  let retained_query_ms: number | null = null;
  for (const item of request.cases) {
    const started = performance.now();
    let child: ChildProcess | undefined,
      closed = false,
      argv: readonly string[] = [];
    try {
      const value = await query_services('user', item.timeout, item.limit, (args) => {
        argv = args;
        const current = spawn(
          process.execPath,
          [request.script, item.mode, ...(item.completion ? [item.completion] : [])],
          {
            stdio: ['ignore', 'pipe', 'ignore'],
            shell: false,
          },
        );
        child = current;
        current.on('close', () => {
          closed = true;
        });
        return current;
      });
      results.push({ mode: item.mode, value, error: null, closed, argv });
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      results.push({
        mode: item.mode,
        value: null,
        error: { name: error.name, message: error.message },
        closed,
        argv,
      });
    }
    if (child && !closed)
      throw new Error('service command returned before its exact child settled');
    if (item.completion) {
      retained_query_ms = performance.now() - started;
      (results.at(-1) as Record<string, unknown>).returned_before_descendant_expiry = !existsSync(
        item.completion,
      );
      const deadline = performance.now() + 2000;
      while (!existsSync(item.completion) && performance.now() < deadline) await pause(10);
      if (!existsSync(item.completion)) throw new Error('scripted descendant did not self-expire');
      await pause(25);
    }
  }
  return encode({ results, retained_query_ms });
}
