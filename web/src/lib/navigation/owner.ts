import type { Fence, RevisionBatch, SnapshotResponse } from './contracts';
import type { NavigationCollectionAdapter, NavigationPublication } from './collection';
import { compareRevision, decodeCursor, encodeCursor, parseRevisionBatch, parseSnapshotResponse } from './validation';
import { navigationConfiguration } from './configuration';
import type { NavigationTransport } from './transport';
import { createVisibilityTracker, type Certification } from './visibility';

export type NavigationOwnerState = {
  connection: 'idle' | 'snapshot' | 'subscribed' | 'reconnecting' | 'disposed';
  generation: number;
  publication: NavigationPublication | undefined;
};

/** Create once above routes. Only this owner admits canonical collection transactions. */
export function createNavigationOwner(options: {
  adapter: NavigationCollectionAdapter;
  transport: NavigationTransport;
  maximumPendingBatches?: number;
  reconnectDelayMs?: number;
}) {
  const { adapter, transport } = options;
  let state: NavigationOwnerState = { connection: 'idle', generation: 0, publication: undefined };
  const listeners = new Set<(state: NavigationOwnerState) => void>();
  const pendingPublications = new Set<{ cursor: string; resolve(publication: NavigationPublication): void; reject(error: Error): void }>();
  let controller = new AbortController();
  let closeStream: (() => void) | undefined;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let started = false;
  let serial: Promise<unknown> = Promise.resolve();
  let pending = 0;
  let paused = false;
  let transitionRequest = 0;
  const maximumPending = options.maximumPendingBatches ?? navigationConfiguration.maximumPendingBatches;
  if (!Number.isInteger(maximumPending) || maximumPending < 1) throw new Error('invalid navigation queue limit');
  const visibility = createVisibilityTracker((fence) => {
    // A publication can request certification inside the adapter callback. Finish
    // that publication's serial task before a new recovery invalidates its generation.
    const prior = serial;
    return prior.then(() => recover(fence));
  });
  function emit() { for (const listener of listeners) listener(state); }
  function update(connection: NavigationOwnerState['connection']) { state = { ...state, connection }; emit(); }
  function current(generation: number) { return !disposed && state.generation === generation; }
  async function nextGeneration(connection: NavigationOwnerState['connection']) {
    const request = ++transitionRequest;
    paused = true;
    controller.abort();
    closeStream?.();
    closeStream = undefined;
    clearTimeout(reconnectTimer);
    // An admitted provider transaction cannot be canceled. Keep its generation
    // valid until the real collection publishes it. No new frame can enter here.
    await serial;
    if (disposed || request !== transitionRequest) throw new Error('obsolete navigation transition');
    controller = new AbortController();
    paused = false;
    state = { ...state, generation: state.generation + 1, connection };
    visibility.generation(state.generation);
    emit();
    return state.generation;
  }
  const unsubscribe = adapter.subscribe((publication) => {
    for (const operation of [...pendingPublications]) {
      if (operation.cursor === publication.cursor) {
        pendingPublications.delete(operation);
        operation.resolve(publication);
      }
    }
  });
  function publish(cursor: string, apply: () => void, signal: AbortSignal, generation: number): Promise<NavigationPublication> {
    return new Promise((resolve, reject) => {
      const abort = () => {
        // Abort only admission that never reached the provider commit queue.
        if (adapter.isPublicationPending(cursor)) return;
        pendingPublications.delete(operation);
        reject(new Error('obsolete navigation publication'));
      };
      const operation = {
        cursor,
        resolve(publication: NavigationPublication) {
          signal.removeEventListener('abort', abort);
          acceptPublication(publication, generation);
          resolve(publication);
        },
        reject(error: Error) { signal.removeEventListener('abort', abort); reject(error); },
      };
      if (signal.aborted) { reject(new Error('obsolete navigation publication')); return; }
      signal.addEventListener('abort', abort, { once: true });
      pendingPublications.add(operation);
      try { apply(); } catch (error) {
        pendingPublications.delete(operation);
        signal.removeEventListener('abort', abort);
        reject(error);
      }
    });
  }
  function acceptPublication(publication: NavigationPublication, generation: number) {
    if (!current(generation)) return;
    state = { ...state, publication };
    visibility.publication(publication);
    emit();
  }
  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const task = serial.then(operation);
    serial = task.catch(() => undefined);
    return task;
  }
  function sameDomain(left: { scope: string; schema_version: number; semantics: string }, right: { scope: string; schema_version: number; semantics: string }) {
    return left.scope === right.scope && left.schema_version === right.schema_version && left.semantics === right.semantics;
  }
  function connect(generation: number) {
    if (!current(generation) || !state.publication) return;
    const signal = controller.signal;
    update('subscribed');
    closeStream = transport.connect(state.publication.cursor, {
      batch(data, eventCursor) {
        if (!current(generation) || paused) return;
        if (pending >= maximumPending) { void recover().catch(() => undefined); return; }
        pending += 1;
        void enqueue(async () => {
          try {
            if (!current(generation) || paused) return;
            const batch: RevisionBatch = parseRevisionBatch(data);
            const cursor = decodeCursor(eventCursor);
            // Validate complete continuity before duplicate suppression, including the event id.
            if (encodeCursor(batch) !== eventCursor || !sameDomain(cursor, batch) || cursor.epoch !== batch.epoch || cursor.revision !== batch.revision) {
              throw new Error('navigation event cursor mismatch');
            }
            const previous = state.publication;
            if (!previous || !sameDomain(previous.continuity, batch) || previous.continuity.epoch !== batch.epoch) {
              throw new Error('navigation continuity changed');
            }
            if (compareRevision(batch.revision, previous.continuity.revision) <= 0) return;
            if (batch.base_revision !== previous.continuity.revision || BigInt(batch.revision) !== BigInt(batch.base_revision) + 1n) {
              throw new Error('navigation journal gap');
            }
            await publish(eventCursor, () => adapter.publishBatch(batch, eventCursor, signal), signal, generation);
          } finally { pending -= 1; }
        }).catch(() => { if (current(generation) && !paused) void recover().catch(() => undefined); });
      },
      reset() { if (current(generation) && !paused) void recover().catch(() => undefined); },
      error() {
        if (!current(generation) || paused) return;
        void reconnect().catch(() => undefined);
      },
    }, signal);
  }
  async function reconnect() {
        const next = await nextGeneration('reconnecting');
        // Drain admitted transactions before reconnect: only published cursors establish resume.
        reconnectTimer = setTimeout(() => {
          void enqueue(async () => {
            if (!current(next)) return;
            const published = adapter.getPublication();
            if (published) acceptPublication(published, next);
            connect(next);
          });
        }, options.reconnectDelayMs ?? navigationConfiguration.reconnectDelayMs);
  }
  async function recover(fence?: Fence): Promise<Certification> {
    if (disposed) return Promise.reject(new Error('navigation owner is disposed'));
    const generation = await nextGeneration('snapshot');
    const signal = controller.signal;
    // Keep the complete prior view while draining accepted publication and replacing it.
    return enqueue(async () => {
      if (!current(generation)) throw new Error('obsolete navigation generation');
      const response: SnapshotResponse = parseSnapshotResponse(await transport.snapshot(signal, fence));
      if (!current(generation) || paused) throw new Error('obsolete navigation snapshot');
      const previous = adapter.getPublication();
      if (previous && (!sameDomain(previous.continuity, response.snapshot) ||
          (previous.continuity.epoch === response.snapshot.epoch && compareRevision(response.snapshot.revision, previous.continuity.revision) < 0))) {
        throw new Error('navigation snapshot continuity mismatch');
      }
      const publication = await publish(response.cursor, () => adapter.publishSnapshot(response.snapshot, response.cursor, signal), signal, generation);
      if (!current(generation)) throw new Error('obsolete navigation publication');
      connect(generation);
      return { publication, proof: response.proof };
    }).catch((error) => {
      if (current(generation) && !paused) {
        update('reconnecting');
        reconnectTimer = setTimeout(() => {
          if (current(generation)) void recover().catch(() => undefined);
        }, options.reconnectDelayMs ?? navigationConfiguration.reconnectDelayMs);
      }
      throw error;
    });
  }
  return {
    start() {
      if (started || disposed) return Promise.resolve();
      started = true;
      return recover().then(() => undefined);
    },
    recover: () => recover().then(() => undefined),
    getState: () => state,
    subscribe(listener: (state: NavigationOwnerState) => void) {
      listeners.add(listener);
      listener(state);
      return () => { listeners.delete(listener); };
    },
    registerObserver: visibility.registerObserver,
    waitForVisibility: visibility.waitForVisibility,
    dispose() {
      if (disposed) return;
      disposed = true;
      controller.abort();
      closeStream?.();
      clearTimeout(reconnectTimer);
      unsubscribe();
      for (const operation of pendingPublications) operation.reject(new Error('navigation owner disposed'));
      pendingPublications.clear();
      visibility.dispose();
      update('disposed');
      listeners.clear();
    },
  };
}
