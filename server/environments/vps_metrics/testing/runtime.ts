/** Explicit connected proofs use the actual pool and sampler, without another HTTP host. */
import { appendFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { metrics_config } from '../configuration.ts';
import type { MetricsConfig } from '../configuration.ts';
import { decode, encode, float } from '../codec.ts';
import type { Collector, Sample } from '../contracts.ts';
import { MetricsRuntime } from '../runtime.ts';
import type { BuildCollector } from '../sampler/contracts.ts';

interface Probe {
  root: string;
  database: string;
  sample: Sample;
  config?: Partial<MetricsConfig>;
  mode: 'plain' | 'sampler' | 'hung' | 'short_hung' | 'startup_failure' | 'blocked_read';
}
interface ScriptOptions {
  root: string;
  sample: string;
  gate?: string;
  fail?: boolean;
}
export const scripted_collector: BuildCollector = (raw): Collector => {
  const options = raw as ScriptOptions;
  const sample = decode<Sample>(options.sample);
  let calls = 0;
  return {
    async collect({ include_slow }) {
      const call = ++calls;
      appendFileSync(
        options.root + '/collection.jsonl',
        encode({ type: 'start', call, include_slow, wall: Date.now() / 1000 }) + '\n',
      );
      while (options.gate && !existsSync(options.gate)) await delay(5);
      if (options.fail && call === 1) throw new Error('scripted collector failure');
      const current = decode<Sample>(encode(sample));
      current.timestamp = float(Date.now() / 1000);
      current.monotonic = float(performance.now() / 1000);
      appendFileSync(
        options.root + '/collection.jsonl',
        encode({ type: 'finish', call, wall: Date.now() / 1000 }) + '\n',
      );
      return current;
    },
  };
};
export const failing_factory: BuildCollector = () => {
  throw new Error('scripted startup failure');
};
function emit(value: unknown): void {
  process.stdout.write(encode(value) + '\n');
}
export async function metrics_runtime_probe(input: string): Promise<string> {
  const spec = decode<Probe>(input);
  // Configuration is control data, not serialized sample numbers.
  const config = metrics_config((JSON.parse(input) as Probe).config ?? {});
  const factory =
    spec.mode === 'plain' || spec.mode === 'blocked_read'
      ? null
      : {
          module_url: import.meta.url,
          export_name: spec.mode === 'startup_failure' ? 'failing_factory' : 'scripted_collector',
          options: {
            root: spec.root,
            sample: encode(spec.sample),
            ...(spec.mode === 'hung' || spec.mode === 'short_hung'
              ? { gate: spec.root + '/gate' }
              : {}),
            ...(spec.mode === 'sampler' ? { fail: true } : {}),
          },
        };
  const runtime = new MetricsRuntime({
    path: spec.database,
    config,
    collector: factory,
    wall_clock: () => 100,
  });
  const before = performance.now(),
    rss_before = process.memoryUsage().rss;
  try {
    await runtime.start();
  } catch (error) {
    await runtime.stop({ deadline: performance.now() / 1000 + 1 });
    await runtime.settled();
    return encode({ startup_error: (error as Error).message });
  }
  const startup_ms = performance.now() - before,
    rss_ready = process.memoryUsage().rss;
  if (spec.mode === 'plain') {
    emit({ type: 'ready_cost', startup_ms, rss_before, rss_ready });
    while (!existsSync(spec.root + '/cost-ack')) await delay(5);
    const cold = performance.now(),
      latest = await runtime.latest(),
      cold_ms = performance.now() - cold;
    const warm_ms: number[] = [];
    for (let i = 0; i < 15; i++) {
      const began = performance.now();
      await runtime.latest();
      warm_ms.push(performance.now() - began);
    }
    const activity_start = performance.now(),
      activity = await runtime.renew_activity();
    const activity_ms = performance.now() - activity_start;
    const page = await runtime.read({
      kind: 'processes',
      now: 100,
      query: { sequence: 1, offset: 0, limit: 250 },
    });
    const history = await runtime.read({
      kind: 'history',
      query: { signal: 'cpu', entity: null, start: 0, end: 100, resolution: 'minute' },
    });
    const stopping = performance.now();
    await runtime.stop({ deadline: performance.now() / 1000 + 1 });
    await runtime.settled();
    return encode({
      latest,
      activity,
      page,
      history,
      startup_ms,
      rss_before,
      rss_ready,
      cold_ms,
      warm_ms,
      activity_ms,
      stop_ms: performance.now() - stopping,
    });
  }
  if (spec.mode === 'startup_failure') throw new Error('startup unexpectedly passed');
  if (spec.mode === 'blocked_read') {
    // The Python fixture replaces samples with a recursive view after READY.
    emit({ type: 'ready', pid: process.pid });
    while (!existsSync(spec.root + '/query-ready')) await delay(5);
    const read = runtime.latest().then(
      () => 'unexpected',
      (error: Error) => error.message,
    );
    const queued = runtime.latest().then(
      () => 'unexpected',
      (error: Error) => error.message,
    );
    await delay(30);
    emit({ type: 'blocking', at: Date.now() / 1000 });
    const until = performance.now() + 1200;
    while (performance.now() < until) {
      /* synthetic reproduction of measured rebuild stalls */
    }
    const results = await Promise.all([read, queued]);
    await runtime.stop({ deadline: performance.now() / 1000 + 1 });
    await runtime.settled();
    return encode({ results, stopped: true });
  }
  while (!existsSync(spec.root + '/collection.jsonl')) await delay(5);
  if (spec.mode === 'hung' || spec.mode === 'short_hung') {
    let contender: MetricsRuntime | undefined;
    if (spec.mode === 'hung') {
      const root = spec.root + '/contender';
      mkdirSync(root);
      contender = new MetricsRuntime({
        path: spec.database,
        config: { ...config, read_workers: 1 },
        collector: {
          module_url: import.meta.url,
          export_name: 'scripted_collector',
          options: { root, sample: encode(spec.sample), gate: root + '/gate' },
        },
      });
      await contender.start();
      if (contender.owns_collection || existsSync(root + '/collection.jsonl'))
        throw new Error('two metrics samplers collected at once');
    }
    const began = performance.now();
    await runtime.stop({ deadline: performance.now() / 1000 + 0.03 });
    emit({
      type: 'stopped_pending',
      owner: runtime.owns_collection,
      stop_ms: performance.now() - began,
    });
    if (spec.mode === 'short_hung') return encode({ unref_exit: true });
    while (!existsSync(spec.root + '/release')) await delay(5);
    writeFileSync(spec.root + '/gate', 'release');
    await runtime.settled();
    while (!existsSync(spec.root + '/contender/collection.jsonl')) await delay(5);
    await contender!.stop({ deadline: performance.now() / 1000 + 0.03 });
    const contender_took_over = contender!.owns_collection;
    writeFileSync(spec.root + '/contender/gate', 'release');
    await contender!.settled();
    return encode({ settled: true, owner: runtime.owns_collection, contender_took_over });
  }
  // The first failure retries on the next cadence. Activity must wake the independent loop.
  await runtime.renew_activity();
  const blocked_at = Date.now() / 1000,
    blocked_until = performance.now() + 160;
  while (performance.now() < blocked_until) {
    /* sampler must continue on its own event loop */
  }
  const unblocked_at = Date.now() / 1000;
  const latest = await runtime.latest();
  await runtime.stop({ deadline: performance.now() / 1000 + 1 });
  await runtime.settled();
  return encode({
    latest,
    failure: runtime.failure,
    owner: runtime.owns_collection,
    startup_ms,
    blocked_at,
    unblocked_at,
  });
}
