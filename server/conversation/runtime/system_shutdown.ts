import { recipient_delivery_settlements } from '../../message_delivery/lifetime.ts';
/** Native operation tracking, quiescence, and conversation core shutdown. */
import { observe_shutdown, is_shutdown_business_cancellation } from '../shutdown.ts';
import { detach_children } from './maintenance.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

export function native_operation<T>(
  host: SqliteProcessConversationSystem,
  operation: () => Promise<T>,
): Promise<T> {
  if (host.shutting_down)
    return Promise.reject(new DOMException('The operation was aborted', 'AbortError'));
  let cancel!: () => void;
  const closing = new Promise<T>((_resolve, reject) => {
    cancel = () => reject(new DOMException('The operation was aborted', 'AbortError'));
  });
  host.native_waiters.add(cancel);
  // Reserve before invoking a provider: it can synchronously request shutdown.
  let release!: () => void;
  const actual = new Promise<void>((resolve) => {
    release = resolve;
  });
  host.native_settlements.add(actual);
  const settled = () => {
    host.native_settlements.delete(actual);
    release();
  };
  let native: Promise<T>;
  try {
    native = operation();
  } catch (error) {
    settled();
    host.native_waiters.delete(cancel);
    return Promise.reject(error);
  }
  void Promise.resolve(native).then(settled, settled);
  return Promise.race([native, closing]).finally(() => host.native_waiters.delete(cancel));
}

export async function wait_until_quiescent(host: SqliteProcessConversationSystem): Promise<void> {
  while (true) {
    const tasks = [
      ...recipient_delivery_settlements(host),
      ...[...host.conversations.values()].flatMap((state) => [
        ...state.settlement_tasks,
        ...(state.backend_event_pump ? [state.backend_event_pump] : []),
      ]),
    ];
    if (tasks.length === 0) return;
    await Promise.allSettled(tasks);
  }
}

export function settled(host: SqliteProcessConversationSystem): Promise<void> {
  return host.shutdown_task ?? Promise.resolve();
}

export function shutdown(
  host: SqliteProcessConversationSystem,
  options: import('../../lifecycle/contracts.ts').StopOptions,
): Promise<void> {
  if (!host.shutdown_task) begin_shutdown(host, options);
  return observe_shutdown(
    host.shutdown_task!,
    options,
    'Shutdown deadline expired; ownership retained: conversation core',
    ['conversation core'],
  );
}

function begin_shutdown(
  host: SqliteProcessConversationSystem,
  options: import('../../lifecycle/contracts.ts').StopOptions,
): void {
  let resolve!: () => void, reject!: (error: unknown) => void;
  host.shutdown_task = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  void host.shutdown_task.catch(() => {});
  host.shutting_down = true;
  host.background_task_notices.seal();
  if (host.deferred_start_timer !== null) clearTimeout(host.deferred_start_timer);
  host.deferred_start_timer = null;
  host.deferred_start_conversations.clear();
  host.workspace_admission.seal();
  const failures: unknown[] = [];
  try {
    host.options.on_shutdown?.();
  } catch (error) {
    failures.push(error);
  }
  const states = [...host.conversations.values()];
  const pumps = states.flatMap((state) =>
    state.backend_event_pump ? [state.backend_event_pump] : [],
  );
  // A child can already exist while its native start still awaits a response.
  for (const state of states) {
    if (state.child !== null) host.child_stop_ownership.set(state.child, state);
    if (state.preparing_child !== null) host.child_stop_ownership.set(state.preparing_child, state);
    if (state.child === null && state.preparing_child !== null) state.child = state.preparing_child;
    state.closing = true;
    state.child_event_sink = null;
    if (state.running_turn) state.running_turn.callbacks_revoked = true;
    state.backend_events.length = 0;
    state.backend_event_pump = null;
    state.late_steer_generation += 1;
    state.late_steers.clear();
    for (const barrier of state.steer_settlements) barrier.resolve();
    state.steer_settlements.clear();
    state.phase_settled.resolve();
    state.reserved_turn?.resolved.resolve();
  }
  for (const cancel of host.native_waiters) cancel();
  host.native_waiters.clear();
  host.shutdown_controller.abort();
  // Detach every child before waiting for a raw call. Exit can settle that call.
  const detaching = detach_children(
    states,
    [...host.child_stop_ownership].map(([child, state]) => ({ child, state })),
    options,
    async (state, child) => {
      if (child.stop_outside_lock) return;
      state.local_child_retirement_proven = true;
    },
  ).then(() => {
    host.child_stop_ownership.clear();
  });
  const janitor = host.idle_janitor?.stop();
  void (async () => {
    const [cleanup, business] = await Promise.all([
      Promise.allSettled([detaching, janitor, ...host.native_settlements]),
      Promise.allSettled([
        ...recipient_delivery_settlements(host),
        ...pumps,
        ...states.flatMap((state) => [...state.settlement_tasks]),
      ]),
    ]);
    // Cancelling our own business observer is expected. A failed native detach,
    // janitor or store cleanup is never excused by its error name.
    for (const result of cleanup) if (result.status === 'rejected') failures.push(result.reason);
    for (const result of business) {
      if (
        result.status === 'rejected' &&
        !(host.shutting_down && is_shutdown_business_cancellation(result.reason))
      )
        failures.push(result.reason);
    }
    try {
      await host.options.store.finish_writes();
    } catch (error) {
      failures.push(error);
    }
    if (failures.length)
      throw new AggregateError(failures, 'Conversation core did not shut down cleanly');
  })().then(resolve, reject);
}
