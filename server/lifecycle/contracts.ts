import type { Database } from '../core/database/contracts.ts';
import type { Config } from '../core/config.ts';
import type { Clock } from '../core/clock.ts';
import type { ChangeSignal } from '../core/changes/index.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';

/** The same absolute monotonic deadline is supplied to every loop. */
export interface StopOptions {
  deadline: number;
}
export interface ComposedBackgroundService {
  start(): void | Promise<void>;
  close(options: StopOptions): void | Promise<void>;
  /** Actual owned work, without a deadline race. */
  settled(): Promise<void>;
}
export interface ShutdownFailure {
  readonly name: string;
  readonly error: unknown;
}
export interface ShutdownState {
  readonly kind: 'running' | 'stopping' | 'closed';
  readonly pending: readonly string[];
  readonly failures: readonly ShutdownFailure[];
}
export interface ShutdownOwner {
  close(options: StopOptions): Promise<void>;
  readonly shutdown_state: ShutdownState;
  /** Actual already-begun work only, without any automatic finalization. */
  readonly shutdown_settled: Promise<void>;
}
/** An invocation-created service, independently of its systemd-run wrapper. */
export interface OwnedWorkerUnit {
  readonly name: string;
  /** Fulfillment confirms inactivity/natural completion or proven never launched. */
  readonly finished: Promise<void>;
}
/** Cleanup of one already-retained unit cannot launch new application work. */
export interface OwnedUnitControl {
  spawn(action: 'show' | 'stop' | 'reset-failed', extra: readonly string[]): ChildProcess;
}
export interface OwnedChildProcesses {
  seal(): void;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
  ): ChildProcessWithoutNullStreams;
  spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess;
  retain_worker_unit(unit: OwnedWorkerUnit): OwnedUnitControl;
  settled(): Promise<void>;
  /** All guarded groups, then one bounded exact-owned unit batch. */
  force_kill(force_deadline: number): void;
}
/** One invocation-owned scope for short-lived probes, separate from core children. */
export interface OwnedOneShotChildren {
  readonly signal: AbortSignal;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
    stop?: (child: ChildProcess) => void,
  ): ChildProcessWithoutNullStreams;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptions,
    stop?: (child: ChildProcess) => void,
  ): ChildProcess;
  seal(): void;
  /** Actual ChildProcess close boundaries, never command-result completion. */
  settled(): Promise<void>;
}
export interface BackgroundCompositionContext {
  database: Database;
  config: Config;
  clock: Clock;
  changes: ChangeSignal;
  conversationSystem?: ConversationSystem;
  assert_database_owned(): void;
}
export type BackgroundServiceFactory = (
  context: BackgroundCompositionContext,
) => ComposedBackgroundService;
export interface RunningServer {
  close(options: StopOptions): Promise<void>;
}
export type ShutdownCompletion = (code: 0 | 1) => void;
export interface ApplicationEntrypoint {
  start(onShutdownComplete?: ShutdownCompletion): Promise<RunningServer>;
}
/** The retained advisory lease shared by environment operators and the future supervisor. */
export interface LifecycleLease {
  close(): void;
}
export class ServerLifecycleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ServerLifecycleError';
  }
}
export class ShutdownDeadlineExceeded extends ServerLifecycleError {
  readonly pending: readonly string[];
  constructor(pending: readonly string[], message?: string) {
    super(message ?? `Shutdown deadline expired; ownership retained: ${pending.join(', ')}`);
    this.name = 'ShutdownDeadlineExceeded';
    this.pending = pending;
  }
}
export class ServerLifecycleAlreadyOwnedError extends ServerLifecycleError {
  constructor(message: string) {
    super(message);
    this.name = 'ServerLifecycleAlreadyOwnedError';
  }
}
