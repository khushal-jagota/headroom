import type { HistoryQuery, MetricsErrorWire, ProcessQuery } from '../contracts.ts';
import type { MetricsConfig } from '../configuration.ts';

/** Strings cross IPC so that JSON float/integer tokens survive the process boundary. */
export type MetricsRead =
  | { kind: 'latest'; now: number }
  | { kind: 'processes'; now: number; query: ProcessQuery }
  | { kind: 'history'; query: HistoryQuery };
export interface ReaderBoot {
  incarnation: string;
  path: string;
  config: MetricsConfig;
}
export interface ReadJob {
  type: 'read';
  incarnation: string;
  request: string;
  deadline: string;
  operation: MetricsRead;
}
export type ReaderReply =
  | { type: 'ready'; incarnation: string }
  | { type: 'result'; incarnation: string; request: string; payload: string }
  | { type: 'error'; incarnation: string; request: string; error: MetricsErrorWire };
export type WatchdogCommand =
  | { type: 'arm'; incarnation: string; request: string; deadline: string }
  | { type: 'disarm'; incarnation: string; request: string };
export interface WatchdogReply {
  type: 'ready' | 'armed' | 'disarmed';
  incarnation: string;
  request?: string;
}
