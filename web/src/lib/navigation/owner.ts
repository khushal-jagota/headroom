import type { Fence, SnapshotResponse, CurrentAuthority, DegradedAuthority } from './contracts';
import type { NavigationCollectionAdapter, NavigationPublication } from './collection';
import { compareRevision, assertCursorMatches, parseBatchEnvelope, parseSnapshotResponse, sameAuthority } from './validation';
import { navigationConfiguration } from './configuration';
import type { NavigationTransport } from './transport';
import { createVisibilityTracker, type Certification, type VisibilityResult } from './visibility';
import { createNavigationKnowledge, type DurableFact, type EffectiveNavigationView } from './knowledge';

export type NavigationOwnerState = {
  connection: 'idle' | 'snapshot' | 'subscribed' | 'reconnecting' | 'degraded' | 'disposed';
  generation: number;
  publication: NavigationPublication | undefined;
  effective: EffectiveNavigationView | undefined;
};

/** Create once above routes. Only this owner admits canonical collection transactions. */
export function createNavigationOwner(options: {
  adapter: NavigationCollectionAdapter;
  transport: NavigationTransport;
  maximumPendingBatches?: number;
  reconnectDelayMs?: number;
}) {
  const { adapter, transport } = options;
  const knowledge = createNavigationKnowledge();
  let state: NavigationOwnerState = { connection:'idle',generation:0,publication:undefined,effective:undefined };
  const listeners = new Set<(state: NavigationOwnerState) => void>();
  const pendingPublications = new Set<{ token: string; resolve(publication: NavigationPublication): void; reject(error: Error): void }>();
  let controller = new AbortController();
  let closeStream: (() => void) | undefined;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let started = false;
  let serial: Promise<unknown> = Promise.resolve();
  let pending = 0;
  let paused = false;
  let admission = 0;
  const retiredSessions = new Set<string>();
  let lastAuthority: CurrentAuthority | DegradedAuthority | undefined;
  const maximumPending = options.maximumPendingBatches ?? navigationConfiguration.maximumPendingBatches;
  if (!Number.isInteger(maximumPending) || maximumPending < 1) throw new Error('invalid navigation queue limit');
  const visibility = createVisibilityTracker((fence) => {
    const prior = serial;
    return prior.then(() => recover(fence));
  });
  function emit() { for (const listener of listeners) listener(state); }
  function update(connection: NavigationOwnerState['connection']) { state = { ...state,connection }; emit(); }
  function current(generation: number) { return !disposed && state.generation === generation; }
  async function nextGeneration(connection: NavigationOwnerState['connection']) {
    // Loss must suppress runtime knowledge before any uncancellable provider commit drains.
    const generation = state.generation + 1;
    paused = true;
    controller.abort();
    closeStream?.();
    closeStream = undefined;
    clearTimeout(reconnectTimer);
    controller = new AbortController();
    const signal = controller.signal;
    visibility.loseRuntime();
    visibility.generation(generation);
    state = { ...state,generation,connection,effective:knowledge.loseRuntime() };
    emit();
    await serial;
    if (!current(generation)) throw new Error('obsolete navigation transition');
    paused = false;
    return {generation,signal};
  }
  const unsubscribe = adapter.subscribe((publication) => {
    for (const operation of [...pendingPublications]) {
      if (operation.token === publication.admissionToken) {
        pendingPublications.delete(operation);
        operation.resolve(publication);
      }
    }
  });
  function publish(apply: (token: string) => void, signal: AbortSignal, generation: number): Promise<NavigationPublication> {
    const token = `${generation}:${++admission}`;
    return new Promise((resolve,reject) => {
      const abort = () => {
        if (adapter.isPublicationPending(token)) return;
        pendingPublications.delete(operation);
        reject(new Error('obsolete navigation publication'));
      };
      const operation = {
        token,
        resolve(publication: NavigationPublication) {
          signal.removeEventListener('abort',abort);
          if (current(generation) && !paused) acceptPublication(publication);
          resolve(publication);
        },
        reject(error: Error) { signal.removeEventListener('abort',abort); reject(error); },
      };
      if (signal.aborted) { reject(new Error('obsolete navigation publication')); return; }
      signal.addEventListener('abort',abort,{once:true});
      pendingPublications.add(operation);
      try { apply(token); } catch (error) {
        pendingPublications.delete(operation);
        signal.removeEventListener('abort',abort);
        reject(error);
      }
    });
  }
  function canCertify(): boolean {
    return state.effective?.runtimeCurrent === true && [...state.effective.conversations.values()].every(value =>
      value.message_for_owner.state === 'fresh' && value.last_turn_failed.state === 'fresh');
  }
  function acceptPublication(publication: NavigationPublication) {
    state = {...state,publication,effective:knowledge.accept(publication)};
    if (canCertify()) visibility.publication(publication);
    emit();
  }
  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const task = serial.then(operation);
    serial = task.catch(() => undefined);
    return task;
  }
  function sameDomain(left: {scope:string;schema_version:number;semantics:string},right: {scope:string;schema_version:number;semantics:string}) {
    return left.scope === right.scope && left.schema_version === right.schema_version && left.semantics === right.semantics;
  }
  function scheduleRecovery(generation: number) {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      if (current(generation)) void recover().catch(() => undefined);
    },options.reconnectDelayMs ?? navigationConfiguration.reconnectDelayMs);
  }
  function connect(generation: number) {
    const publication = state.publication;
    if (!current(generation) || publication?.mode !== 'current' || publication.cursor === null || state.effective?.runtimeCurrent !== true) return;
    const signal = controller.signal;
    const authority = publication.authority;
    update('subscribed');
    closeStream = transport.connect(publication.cursor,{
      batch(data,eventCursor) {
        if (!current(generation) || paused) return;
        try {
          // Reject contradictory authority synchronously, including duplicate revisions.
          const envelope = parseBatchEnvelope(data);
          if (!sameAuthority(authority,envelope.authority)) throw new Error('navigation authority changed');
          assertCursorMatches(eventCursor,envelope.batch);
          if (pending >= maximumPending) throw new Error('navigation queue overflow');
          pending += 1;
          void enqueue(async () => {
            try {
              if (!current(generation) || paused) return;
              const batch = envelope.batch;
              const previous = state.publication;
              if (!previous || previous.mode !== 'current' || !sameAuthority(previous.authority,envelope.authority)
                || !sameDomain(previous.continuity,batch) || previous.continuity.epoch !== batch.epoch) throw new Error('navigation continuity changed');
              if (compareRevision(batch.revision,previous.continuity.revision) <= 0) return;
              if (batch.base_revision !== previous.continuity.revision) throw new Error('navigation journal gap');
              await publish(token => adapter.publishBatch(envelope,eventCursor,token,signal),signal,generation);
            } finally { pending -= 1; }
          }).catch(() => { if (current(generation) && !paused) void recover().catch(() => undefined); });
        } catch { void recover().catch(() => undefined); }
      },
      reset() { if (current(generation) && !paused) void recover().catch(() => undefined); },
      error() { if (current(generation) && !paused) void recover().catch(() => undefined); },
    },signal);
  }
  function checkAuthority(value: CurrentAuthority | DegradedAuthority) {
    if (retiredSessions.has(value.delivery_session_id)) throw new Error('obsolete delivery session');
    if (lastAuthority?.delivery_session_id === value.delivery_session_id
      && compareRevision(value.availability.generation,lastAuthority.availability.generation) < 0) throw new Error('obsolete availability generation');
  }
  async function recover(fence?: Fence): Promise<Certification> {
    if (disposed) throw new Error('navigation owner is disposed');
    const {generation,signal} = await nextGeneration('snapshot');
    try {
      // HTTP awaits stay outside the provider queue. Only admitted commits must drain.
      const response: SnapshotResponse = parseSnapshotResponse(await transport.snapshot(signal,fence));
      if (!current(generation) || paused) throw new Error('obsolete navigation snapshot');
      checkAuthority(response.authority);
      const previous = state.publication;
      if (previous && (!sameDomain(previous.continuity,response.snapshot)
        || (previous.continuity.epoch === response.snapshot.epoch && compareRevision(response.snapshot.revision,previous.continuity.revision) < 0))) {
        throw new Error('navigation snapshot continuity mismatch');
      }
      const publication = await enqueue(async () => {
        if (!current(generation) || paused) throw new Error('obsolete navigation snapshot');
        return publish(token => adapter.publishSnapshot(response,token,signal),signal,generation);
      });
      if (!current(generation) || paused) throw new Error('obsolete navigation publication');
      if (lastAuthority && lastAuthority.delivery_session_id !== response.authority.delivery_session_id) retiredSessions.add(lastAuthority.delivery_session_id);
      lastAuthority = response.authority;
      if (response.mode === 'current') connect(generation);
      else { update('degraded'); scheduleRecovery(generation); }
      return {publication,proof:canCertify() ? response.proof : null};
    } catch (error) {
      if (current(generation) && !paused) {
        // Failed requests are an observed authority loss even if collection state remains.
        state = {...state,effective:knowledge.loseRuntime()};
        visibility.loseRuntime();
        update('reconnecting');
        scheduleRecovery(generation);
      }
      throw error;
    }
  }
  return {
    start() {
      if (started || disposed) return Promise.resolve();
      started = true;
      return recover().then(() => undefined);
    },
    recover: () => recover().then(() => undefined),
    invalidateDurableFact(conversationId: string, fact: DurableFact, confirmedThroughSequence: number) {
      const effective = knowledge.invalidateDurableFact(conversationId,fact,confirmedThroughSequence);
      if (effective !== state.effective) visibility.loseRuntime();
      state = {...state,effective};
      emit();
    },
    getState: () => state,
    subscribe(listener: (state: NavigationOwnerState) => void) {
      listeners.add(listener); listener(state);
      return () => { listeners.delete(listener); };
    },
    registerObserver: visibility.registerObserver,
    waitForVisibility(value: unknown, options: Parameters<typeof visibility.waitForVisibility>[1]): Promise<VisibilityResult> {
      if (!canCertify()) return Promise.resolve({status:'unknown',reason:'runtime_unavailable'});
      return visibility.waitForVisibility(value,options);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      controller.abort(); closeStream?.(); clearTimeout(reconnectTimer); unsubscribe();
      for (const operation of pendingPublications) operation.reject(new Error('navigation owner disposed'));
      pendingPublications.clear(); visibility.dispose(); knowledge.clear();
      state = {...state,effective:undefined}; update('disposed'); listeners.clear();
    },
  };
}
