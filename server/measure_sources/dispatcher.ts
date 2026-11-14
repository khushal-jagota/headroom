/** Claim at most two runs; each source owns one execution through its final write. */
import type { OwnedAreaContext } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import type { RuntimeLimits, SourceRun, SourceRuntime, RunResult } from './contracts.ts';
import { failure } from './contracts.ts';
import { DEFAULT_LIMITS, MAX_CONCURRENT_RUNS } from './configuration.ts';
import { prepare_admission } from './admission.ts';
import { claim_run, complete_run, interrupt_abandoned_runs } from './run_actions.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
export interface SourceDispatcherOptions {
  context: OwnedAreaContext;
  runtime: SourceRuntime;
  runtime_limits?: RuntimeLimits;
}
export class SourceDispatcher {
  private readonly options: SourceDispatcherOptions;
  private stopped = false;
  private started = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private unsubscribe: (() => void) | undefined;
  private readonly runs = new Map<string, Promise<void>>();
  private closing: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
  constructor(options: SourceDispatcherOptions) {
    this.options = options;
  }
  start(): void {
    const { context } = this.options;
    context.assert_database_owned();
    if (this.started || this.stopped)
      throw new Error('source dispatcher already started or stopped');
    this.started = true;
    context.database.write((sql) =>
      interrupt_abandoned_runs(sql, { now: context.clock.nowUnix() }),
    );
    this.unsubscribe = context.changes.subscribe(() => this.wake());
    this.wake();
  }
  /** This schedules work only after the current transaction and callback return. */
  wake(): void {
    if (!this.started || this.stopped) return;
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      try {
        this.poll_once();
      } catch {
        console.error('source dispatcher poll failed');
      } finally {
        if (!this.stopped && this.timer === undefined) {
          this.timer = setTimeout(() => this.wake(), 5000);
          this.timer.unref();
        }
      }
    }, 0);
    this.timer.unref();
  }
  poll_once(): string[] {
    const { context } = this.options;
    context.assert_database_owned();
    if (this.stopped) return [];
    const remaining = MAX_CONCURRENT_RUNS - this.runs.size;
    if (remaining <= 0) return [];
    const rows = context.database.read((sql) =>
      sql.all<{ id: string }>(
        `
      SELECT p.id FROM measure_source_runs p
      WHERE p.status='pending' AND NOT EXISTS (
        SELECT 1 FROM measure_source_runs r
        WHERE r.source_id=p.source_id AND r.status='running'
      )
      ORDER BY p.created_at,p.rowid LIMIT ?
    `,
        [remaining],
      ),
    );
    const claimed: string[] = [];
    for (const row of rows) {
      if (this.stopped) break;
      const admission = context.conversationSystem?.workspace_admission?.operation();
      if (admission && !admission.admitted) {
        admission.release();
        break;
      }
      let run: SourceRun | null;
      try {
        run = context.database.write((sql) =>
          claim_run(sql, row.id, { now: context.clock.nowUnix() }),
        );
      } catch (error) {
        admission?.release();
        throw error;
      }
      if (run === null) {
        admission?.release();
        continue;
      }
      claimed.push(run.id);
      // Register before execution can finish or cleanup can release workspace admission.
      const task = Promise.resolve()
        .then(() => this.execute(run))
        .finally(() => {
          admission?.release();
          this.runs.delete(run.id);
          this.wake();
        });
      this.runs.set(run.id, task);
      void task.catch(() => console.error('source completion refused'));
    }
    return claimed;
  }
  private async execute(run: SourceRun): Promise<void> {
    const { context, runtime } = this.options;
    let result: RunResult;
    try {
      context.assert_database_owned();
      const [admission, credentials] = prepare_admission(context.database, run, {
        key_path: context.config.credential_key_path,
        limits: this.options.runtime_limits ?? {
          ...DEFAULT_LIMITS,
          wall_seconds: context.config.measure_source_timeout_seconds,
        },
      });
      if (this.stopped) runtime.cancel(run.id);
      result = await runtime.execute(admission, credentials);
    } catch (error) {
      result = {
        observations: [],
        dataset_results: [],
        failure: failure(
          error instanceof PlannerError && error.code === 'stale_claim'
            ? 'stale_definition'
            : error instanceof PlannerError
              ? 'credential_unavailable'
              : 'runtime_failure',
        ),
        metrics: {
          request_count: 0,
          response_bytes: 0,
          child_output_bytes: 0,
          elapsed_milliseconds: 0,
          script_cpu_milliseconds: 0,
        },
      };
    }
    context.assert_database_owned();
    try {
      context.database.write((sql) =>
        complete_run(sql, run.id, result, { now: context.clock.nowUnix() }),
      );
    } catch (error) {
      if (!(error instanceof PlannerError) || error.code !== 'stale_claim') throw error;
    }
  }
  private async drain(): Promise<void> {
    const failures: unknown[] = [];
    while (this.runs.size) {
      const results = await Promise.allSettled([...this.runs.values()]);
      for (const result of results) if (result.status === 'rejected') failures.push(result.reason);
    }
    if (failures.length)
      throw new AggregateError(failures, 'Source runtimes did not settle cleanly');
  }
  settled(): Promise<void> {
    return this.shutdown_settlement ?? this.drain();
  }
  stop(options: StopOptions): Promise<void> {
    if (this.closing) return this.closing;
    this.stopped = true;
    if (this.timer !== undefined) clearTimeout(this.timer);
    const failures: unknown[] = [];
    this.shutdown_settlement = Promise.resolve().then(async () => {
      try {
        await this.drain();
      } catch (error) {
        failures.push(error);
      }
      if (failures.length) throw new AggregateError(failures, 'Source shutdown failed');
    });
    this.closing = observe_shutdown(
      this.shutdown_settlement,
      options,
      'Shutdown deadline expired; ownership retained: source runtimes',
      ['source runtimes'],
    );
    try {
      this.unsubscribe?.();
    } catch (error) {
      failures.push(error);
    }
    this.unsubscribe = undefined;
    for (const id of this.runs.keys()) {
      try {
        this.options.runtime.cancel(id);
      } catch (error) {
        failures.push(error);
      }
    }
    return this.closing;
  }
}
