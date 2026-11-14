import type { ScratchRuntime, ScratchRuntimeOptions } from './contracts.ts';
import { CLEANUP_INTERVAL_SECONDS } from './configuration.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';

/** Independent of readiness/dispatch, with Python's initial ten-minute wait. */
export function create_ticket_scratch_runtime(options: ScratchRuntimeOptions): ScratchRuntime {
  const already_kept = new Set<string>();
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  let flight: Promise<void> | undefined;
  let started = false;
  let closing: Promise<void> | undefined;
  let shutdown_settlement: Promise<void> | undefined;
  const schedule = () => {
    if (controller.signal.aborted) return;
    timer = setTimeout(() => {
      timer = undefined;
      flight = Promise.resolve()
        .then(async () => {
          controller.signal.throwIfAborted();
          options.assert_database_owned();
          const result = await options.clean_finished_ticket_scratch(
            options.layout!,
            options.db_path,
            options.now_unix(),
            already_kept,
            { signal: controller.signal },
          );
          if (controller.signal.aborted) return;
          for (const item of result.skipped)
            console.warn(`Ticket scratch kept: ${item.path} (${item.reason})`);
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) console.error('Ticket scratch cleanup failed', error);
        })
        .finally(() => {
          flight = undefined;
          schedule();
        });
    }, CLEANUP_INTERVAL_SECONDS * 1000);
    timer.unref();
  };
  return {
    start() {
      if (started || closing) throw new Error('Ticket scratch runtime already started or stopped');
      started = true;
      if (!options.test_mode && options.layout !== null) schedule();
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
      clearTimeout(timer);
      timer = undefined;
      return closing;
    },
    settled: () => shutdown_settlement ?? Promise.resolve(flight),
  };
}
