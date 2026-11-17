import { monitorEventLoopDelay, performance } from 'node:perf_hooks';
import type { FastifyInstance } from 'fastify';

/** A task that holds the main thread at least this long writes one journal line. */
export const SLOW_TASK_MS = 200;
const SUMMARY_INTERVAL_MS = 60_000;

let rows_touched = 0;
/** Database statements call this with the rows they returned or changed. */
export function count_rows(count: number): void {
  rows_touched += count;
}

const OWN_FRAMES = /core\/(database\/index|slow_tasks)\.ts/;
function caller(): string {
  const frame = (new Error().stack ?? '')
    .split('\n')
    .slice(1)
    .find((line) => !OWN_FRAMES.test(line) && /\.ts:\d+/.test(line));
  return /([^/\s(]+(?:\/[^/\s(]+)*\.ts:\d+)/.exec(frame ?? '')?.[1] ?? 'unknown';
}

function journal(line: Record<string, unknown>): void {
  console.warn(`slow_task ${JSON.stringify(line)}`);
}

/**
 * Run synchronous `work`. If it ran at least SLOW_TASK_MS, write one journal line with
 * the kind, label (the caller when `label` is omitted), duration and rows touched.
 */
export function timed_task<T>(kind: string, work: () => T, label?: string): T {
  const started = performance.now();
  const rows_before = rows_touched;
  try {
    return work();
  } finally {
    const ms = performance.now() - started;
    if (ms >= SLOW_TASK_MS)
      journal({
        kind,
        label: label ?? caller(),
        ms: Math.round(ms),
        rows: rows_touched - rows_before,
      });
  }
}

/** One line per slow HTTP request, plus one event-loop delay line per minute. */
export function install_slow_task_logging(app: FastifyInstance): void {
  const started = new WeakMap<object, number>();
  app.addHook('onRequest', async (request) => {
    started.set(request, performance.now());
  });
  app.addHook('onResponse', async (request, reply) => {
    const start = started.get(request);
    if (start === undefined) return;
    const ms = performance.now() - start;
    if (ms < SLOW_TASK_MS) return;
    if (String(reply.getHeader('content-type') ?? '').startsWith('text/event-stream')) return;
    const length = Number(reply.getHeader('content-length'));
    journal({
      kind: 'http',
      label: `${request.method} ${request.routeOptions.url ?? 'unmatched'}`,
      ms: Math.round(ms),
      ...(Number.isFinite(length) ? { bytes: length } : {}),
    });
  });
  const delay = monitorEventLoopDelay({ resolution: 10 });
  delay.enable();
  const timer = setInterval(() => {
    const to_ms = (nanoseconds: number) => Math.round(nanoseconds / 1e6);
    journal({
      kind: 'event_loop_minute',
      p50_ms: to_ms(delay.percentile(50)),
      p99_ms: to_ms(delay.percentile(99)),
      max_ms: to_ms(delay.max),
    });
    delay.reset();
  }, SUMMARY_INTERVAL_MS);
  timer.unref();
  app.addHook('onClose', async () => {
    clearInterval(timer);
    delay.disable();
  });
}
