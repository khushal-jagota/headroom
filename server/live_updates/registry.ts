import type { LiveOpener, LiveSubscriptionKind } from './contracts.ts';

/** Each feed's own area registers how a socket opens it, next to its old stream route. */
export class LiveSubscriptions {
  readonly #openers = new Map<LiveSubscriptionKind, LiveOpener>();
  register(kind: LiveSubscriptionKind, opener: LiveOpener): void {
    if (this.#openers.has(kind)) throw new Error(`live subscription ${kind} is already registered`);
    this.#openers.set(kind, opener);
  }
  get(kind: string): LiveOpener | undefined {
    return this.#openers.get(kind as LiveSubscriptionKind);
  }
}
