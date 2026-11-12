import { parentPort, workerData } from 'node:worker_threads';
import { acquireLock } from '../../../core/locks/index.ts';
import { Store } from '../data/store.ts';
import { encode, float } from '../codec.ts';
import { error_wire } from '../errors.ts';
import { MetricsOperationalError } from '../contracts.ts';
import type { Collector, CollectionStatus } from '../contracts.ts';
import type { BuildCollector, SamplerBoot, SamplerCommand, SamplerReply } from './contracts.ts';

if (!parentPort) throw new Error('metrics sampler needs its owner');
const port = parentPort;
const boot = workerData as SamplerBoot;
const store = new Store(boot.path, boot.config);
let stopped = false;
let collector: Collector | null = null;
let wake: (() => void) | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
function send(message: SamplerReply): void {
  port.postMessage(message);
}
function reason(error: unknown): string {
  const value = error as Error & { code?: string };
  return `${value.code?.startsWith('SQLITE_') ? 'OperationalError' : value.name}: ${value.message}`;
}
function wait(seconds: number): Promise<void> {
  if (stopped) return Promise.resolve();
  return new Promise((resolve) => {
    wake = resolve;
    timer = setTimeout(() => {
      wake = undefined;
      resolve();
    }, seconds * 1000);
  });
}
port.on('message', (message: SamplerCommand) => {
  if (message.type === 'stop') {
    stopped = true;
    if (timer) clearTimeout(timer);
    wake?.();
    wake = undefined;
    return;
  }
  if (stopped) {
    send({
      type: 'error',
      request: message.request,
      error: error_wire(new MetricsOperationalError('VPS metrics runtime is stopped')),
    });
    return;
  }
  try {
    const expiry = store.renew_activity(float(message.now));
    send({ type: 'activity', request: message.request, expiry: encode(float(expiry)) });
  } catch (error) {
    send({ type: 'error', request: message.request, error: error_wire(error) });
  }
});
async function sample_loop(): Promise<void> {
  let next_sample = 0,
    next_slow = 0,
    next_prune = 0,
    was_active = false;
  while (!stopped) {
    const now = Date.now() / 1000,
      tick = performance.now() / 1000;
    let cadence = boot.config.idle_seconds;
    try {
      const active = store.activity_until() > now;
      cadence = active ? boot.config.active_seconds : boot.config.idle_seconds;
      if (active && !was_active) next_sample = tick;
      was_active = active;
      // A pass already inside collect settles once after stop, as Python's thread does.
      if (tick >= next_sample) {
        const sample = await collector!.collect({ include_slow: tick >= next_slow });
        sample.expected_interval = float(cadence);
        store.append(sample);
        send({ type: 'failure', payload: null });
        if (tick >= next_slow) next_slow = tick + boot.config.slow_seconds;
        next_sample = performance.now() / 1000 + cadence;
      }
      if (tick >= next_prune) {
        store.prune(now);
        next_prune = tick + boot.config.prune_seconds;
      }
    } catch (error) {
      const text = reason(error);
      const failure: CollectionStatus = {
        availability: 'unavailable',
        reason: text,
        last_sample_at: null,
        last_attempt_at: float(now),
        cadence_seconds: float(cadence),
      };
      send({ type: 'failure', payload: encode(failure) });
      try {
        store.record_failure(float(now), text, float(cadence));
      } catch {
        console.warn('VPS metrics failure could not be persisted: ' + text);
      }
      next_sample = performance.now() / 1000 + cadence;
    }
    await wait(boot.config.activity_check_seconds);
  }
}
async function run(): Promise<void> {
  try {
    store.initialize();
    if (boot.collector) {
      const module = (await import(boot.collector.module_url)) as Record<string, BuildCollector>;
      collector = await module[boot.collector.export_name](boot.collector.options, boot.config);
    }
    send({ type: 'ready' });
    while (!stopped && collector) {
      try {
        const lock = acquireLock(boot.path + '.lock');
        if (!lock) {
          await wait(boot.config.ownership_retry_seconds);
          continue;
        }
        send({ type: 'ownership', owner: true });
        try {
          await sample_loop();
        } finally {
          lock.close();
          send({ type: 'ownership', owner: false });
        }
      } catch (error) {
        console.warn('VPS sampler ownership failed: ' + reason(error));
        await wait(boot.config.ownership_retry_seconds);
      }
    }
    if (!collector && !stopped)
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
  } catch (error) {
    send({ type: 'startup_error', message: (error as Error).message });
  } finally {
    store.close();
    send({ type: 'stopped' });
    port.close();
  }
}
void run();
