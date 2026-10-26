import type { Fence, ProofResult } from './contracts';
import type { NavigationPublication } from './collection';
import { compareRevision, parseFence, sameAuthority } from './validation';
import { navigationConfiguration } from './configuration';

export type VisibilityResult = { status: 'included' | 'no_op' | 'unknown'; reason: string | null };
export interface RenderObservation {
  readonly generation: number;
  readonly publication: NavigationPublication;
}
export interface RenderObserver {
  readonly id: string;
  observeSelectors(publication: NavigationPublication, generation: number): RenderObservation | undefined;
  acknowledgeRender(publication: NavigationPublication, observation: RenderObservation | undefined): void;
  dispose(): void;
}
type Observer = { handle: RenderObserver; active: boolean; selectors?: NavigationPublication; rendered?: NavigationPublication; generation: number; observation?: RenderObservation };
type Wait = {
  fence: Fence;
  required: readonly Observer[];
  resolve(result: VisibilityResult): void;
  timer: ReturnType<typeof setTimeout>;
  certificate?: { publication: NavigationPublication; proof: ProofResult };
  certificationEpoch?: string;
  certificationPending?: boolean;
};
export interface Certification {
  publication: NavigationPublication;
  proof: ProofResult | null;
}

/** Checkpoints preserve DOM evidence only for the exact admitted content and authority. */
export function sameRenderedContent(left:NavigationPublication | undefined,right:NavigationPublication | undefined):boolean {
  if(!left || !right) return false;
  if(left===right) return true;
  return !!left.view && !!right.view && left.view.id===right.view.id
    && left.view.scopeGeneration===right.view.scopeGeneration
    && left.view.contentVersion===right.view.contentVersion
    && left.view.orderingGeneration===right.view.orderingGeneration
    && left.continuity.scope===right.continuity.scope && left.continuity.epoch===right.continuity.epoch
    && sameAuthority(left.authority,right.authority);
}

/** Collection, coherent selector and DOM observations are separate pieces of evidence. */
export function createVisibilityTracker(certify: (fence: Fence) => Promise<Certification>) {
  const observers = new Map<RenderObserver, Observer>();
  const activeIds = new Set<string>();
  const waits = new Set<Wait>();
  let publication: NavigationPublication | undefined;
  let generation = 0;
  let disposed = false;
  const unknown = (reason: string): VisibilityResult => ({ status: 'unknown', reason });
  function finish(wait: Wait, result: VisibilityResult) {
    if (!waits.delete(wait)) return;
    clearTimeout(wait.timer);
    wait.resolve(result);
  }
  function inspect(wait: Wait) {
    if (wait.required.some((observer) => !observer.active)) return finish(wait, unknown('observer_removed'));
    if (!publication || publication.mode !== 'current') return;
    const fence = wait.fence;
    const current = publication.continuity;
    if (fence.scope !== current.scope || fence.schema_version !== current.schema_version || fence.semantics !== current.semantics) {
      return finish(wait, unknown('incompatible_scope'));
    }
    if (wait.certificate && !sameAuthority(publication.authority,wait.certificate.publication.authority)) {
      wait.certificate = undefined;
      wait.certificationEpoch = undefined;
    }
    let status: 'included' | 'no_op' | undefined;
    if (wait.certificate) {
      // A certificate establishes history only at its atomically published snapshot.
      const certified = wait.certificate.publication.continuity;
      if (sameAuthority(publication.authority,wait.certificate.publication.authority) && current.epoch === certified.epoch && compareRevision(current.revision, certified.revision) >= 0) {
        if (wait.certificate.proof.status === 'unknown') return finish(wait, unknown(wait.certificate.proof.reason ?? 'uncertified'));
        status = wait.certificate.proof.status;
      }
    } else if (fence.epoch === current.epoch && fence.witnesses.length > 0 && compareRevision(current.revision, fence.revision) >= 0) {
      status = 'included';
    }
    if (!status) {
      if (fence.epoch !== current.epoch || fence.witnesses.length === 0) requestCertification(wait);
      return;
    }
    if (wait.required.every((observer) => observer.generation === generation && sameRenderedContent(observer.selectors,publication) && sameRenderedContent(observer.rendered,publication))) {
      finish(wait, { status, reason: null });
    }
  }
  function requestCertification(wait: Wait) {
    const epoch = publication?.continuity.epoch ?? 'unpublished';
    if (wait.certificationPending || wait.certificationEpoch === epoch) return;
    wait.certificationEpoch = epoch;
    wait.certificationPending = true;
    void Promise.resolve().then(() => certify(wait.fence)).then((result) => {
      if (!waits.has(wait)) return;
      wait.certificationPending = false;
      if (!result.proof) return finish(wait, unknown('missing_proof'));
      wait.certificate = { publication: result.publication, proof: result.proof };
      inspect(wait);
    }, () => finish(wait, unknown('certification_failed')));
  }
  function inspectAll() { for (const wait of [...waits]) inspect(wait); }
  return {
    generation(value: number) {
      generation = value;
      publication = undefined;
      for (const observer of observers.values()) {
        observer.selectors = undefined;
        observer.rendered = undefined;
      }
    },
    loseRuntime() {
      publication = undefined;
      for (const observer of observers.values()) {
        observer.selectors = undefined;
        observer.rendered = undefined;
        observer.observation = undefined;
      }

    },
    publication(value: NavigationPublication) { publication = value.mode === 'current' ? value : undefined; inspectAll(); },
    registerObserver(id: string): RenderObserver {
      if (disposed) throw new Error('navigation owner is disposed');
      if (activeIds.has(id)) throw new Error(`duplicate navigation observer: ${id}`);
      const observer: Observer = { handle: undefined as unknown as RenderObserver, active: true, generation };
      const handle: RenderObserver = Object.freeze({
        id,
        observeSelectors(value: NavigationPublication, observedGeneration: number) {
          if (!observer.active || value.mode !== 'current' || !sameRenderedContent(value,publication) || observedGeneration !== generation) return;
          observer.generation = generation;
          observer.selectors = value;
          observer.rendered = undefined;
          observer.observation = Object.freeze({generation, publication:value});
          return observer.observation;
        },
        acknowledgeRender(value: NavigationPublication, observation: RenderObservation | undefined) {
          if (!observation || observation !== observer.observation || observation.generation !== generation) return;
          if (!observer.active || observer.generation !== generation || !sameRenderedContent(value,publication) || !sameRenderedContent(observer.selectors,value)) return;
          observer.rendered = value;
          inspectAll();
        },
        dispose() {
          if (!observer.active) return;
          observer.active = false;
          activeIds.delete(id);
          observers.delete(handle);
          inspectAll();
        },
      });
      observer.handle = handle;
      observers.set(handle, observer);
      activeIds.add(id);
      return handle;
    },
    waitForVisibility(value: unknown, options: { requiredObservers: readonly RenderObserver[]; timeoutMs?: number }): Promise<VisibilityResult> {
      if (disposed) return Promise.resolve(unknown('disposed'));
      let fence: Fence;
      try { fence = parseFence(value); } catch { return Promise.resolve(unknown('missing_or_invalid_fence')); }
      if (fence.coverage !== 'complete') return Promise.resolve(unknown('incomplete_capture'));
      const required = options.requiredObservers.map((handle) => observers.get(handle));
      if (required.length === 0 || required.some((observer) => !observer?.active)) return Promise.resolve(unknown('missing_observer'));
      return new Promise((resolve) => {
        const wait: Wait = {
          fence, required: Object.freeze(required as Observer[]), resolve,
          timer: setTimeout(() => finish(wait, unknown('timeout')), options.timeoutMs ?? navigationConfiguration.visibilityTimeoutMs),
        };
        waits.add(wait);
        inspect(wait);
        if (!waits.has(wait)) return;
        if (!publication || fence.epoch !== publication.continuity.epoch || fence.witnesses.length === 0) {
          requestCertification(wait);
        }
      });
    },
    dispose() {
      disposed = true;
      for (const observer of observers.values()) observer.active = false;
      for (const wait of [...waits]) finish(wait, unknown('disposed'));
      observers.clear();
      activeIds.clear();
      publication = undefined;
    },
  };
}
