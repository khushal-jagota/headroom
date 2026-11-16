import type { ScratchRuntime, ScratchRuntimeOptions } from './contracts.ts';
import { CLEANUP_INTERVAL_SECONDS } from './configuration.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { createReadAccess } from '../../core/database/index.ts';
import { RetainedWorkspaceReporter } from '../../ticket_workspaces/reporting.ts';

/** Lifecycle changes wake cleanup; unrelated writes retain the periodic deadline. */
export function create_ticket_scratch_runtime(options: ScratchRuntimeOptions): ScratchRuntime {
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  let flight: Promise<void> | undefined;
  let started = false;
  let closing: Promise<void> | undefined;
  let shutdown_settlement: Promise<void> | undefined;
  let unsubscribe: (() => void) | undefined;
  let previous: string | undefined;
  let retry_at = 0;
  let dirty = false;
  const reporter = new RetainedWorkspaceReporter();
  const schedule = () => {
    if (controller.signal.aborted) return;
    clearTimeout(timer);
    timer = setTimeout(wake, Math.max(0, retry_at - performance.now()));
    timer.unref();
  };
  const wake = () => {
    if (controller.signal.aborted) return;
    if (flight) {
      dirty = true;
      return;
    }
    clearTimeout(timer);
    timer = undefined;
    flight = Promise.resolve()
      .then(async () => {
        controller.signal.throwIfAborted();
        options.assert_database_owned();
        const reader = createReadAccess(options.db_path);
        let snapshot: string;
        try {
          snapshot = reader.read((sql) =>
            JSON.stringify(
              sql.all(
                'SELECT ticket_id,ended_at,deleted FROM ticket_workspace_lifecycle WHERE ended_at IS NOT NULL ORDER BY ticket_id',
              ),
            ),
          );
        } finally {
          reader.close();
        }
        if (snapshot === previous && performance.now() < retry_at) return;
        retry_at = performance.now() + CLEANUP_INTERVAL_SECONDS * 1000;
        const result = await options.clean_workspaces(options.db_path, options.now_unix(), {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        reporter.report(result.skipped);
        previous = snapshot;
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error('Ticket scratch cleanup failed', error);
          retry_at = performance.now() + CLEANUP_INTERVAL_SECONDS * 1000;
        }
      })
      .finally(() => {
        flight = undefined;
        if (dirty) {
          dirty = false;
          wake();
        } else schedule();
      });
  };
  return {
    start() {
      if (started || closing) throw new Error('Ticket scratch runtime already started or stopped');
      started = true;
      if (!options.test_mode) {
        unsubscribe = options.changes?.subscribe(wake);
        wake();
      }
    },
    close(stop) {
      if (closing) return closing;
      shutdown_settlement = Promise.resolve().then(() => flight);
      closing = observe_shutdown(
        shutdown_settlement,
        stop,
        'Shutdown deadline expired; ownership retained: ticket scratch',
        ['ticket scratch'],
      );
      controller.abort();
      unsubscribe?.();
      clearTimeout(timer);
      timer = undefined;
      return closing;
    },
    settled: () => shutdown_settlement ?? Promise.resolve(flight),
  };
}
