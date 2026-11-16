import type { MetricsConfig } from '../configuration.ts';
import type { Collector, MetricsErrorWire } from '../contracts.ts';

/** The explicit composition factory runs inside the sampler, never on the app loop. */
export interface CollectorFactory {
  module_url: string;
  export_name: string;
  options?: unknown;
}
export type BuildCollector = (
  options: unknown,
  config: MetricsConfig,
) => Collector | Promise<Collector>;
export interface SamplerBoot {
  path: string;
  config: MetricsConfig;
  collector: CollectorFactory | null;
}
export type SamplerCommand = { type: 'activity'; request: string; now: number } | { type: 'stop' };
export type SamplerReply =
  | { type: 'ready' }
  | { type: 'ownership'; owner: boolean }
  | { type: 'failure'; payload: string | null }
  | { type: 'activity'; request: string; expiry: string }
  | { type: 'error'; request: string; error: MetricsErrorWire }
  | { type: 'startup_error'; message: string }
  | { type: 'stopped' };
