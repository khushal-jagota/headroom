/** Ephemeral publication of committed rows and transient frames. */
import type { ConversationLiveTailFrame } from './events.ts';
import type { StoredConversationEvent } from './record/contracts.ts';
import { ConversationViewLiveOutput } from './views/live_output.ts';
export type ConversationTailItem = StoredConversationEvent | ConversationLiveTailFrame;
export const MAXIMUM_HELD_TAIL_ITEMS = 2048;
export class ConversationTailSubscription {
  readonly conversation_id: string;
  readonly #owner: ConversationLiveTail;
  readonly #items: (ConversationTailItem | null)[] = [];
  #waiting: ((item: ConversationTailItem | null) => void) | null = null;
  #closed = false;
  constructor(owner: ConversationLiveTail, conversation_id: string) {
    this.#owner = owner;
    this.conversation_id = conversation_id;
  }
  deliver(item: ConversationTailItem): void {
    if (this.#closed) {
      return;
    }
    if (this.#items.length >= MAXIMUM_HELD_TAIL_ITEMS) {
      this.close();
      return;
    }
    if (this.#waiting !== null) {
      const waiting = this.#waiting;
      this.#waiting = null;
      waiting(item);
    } else this.#items.push(item);
  }
  close(): void {
    if (this.#closed) {
      return;
    }
    this.#closed = true;
    this.#owner.forget(this);
    if (this.#waiting !== null) {
      const waiting = this.#waiting;
      this.#waiting = null;
      waiting(null);
    } else this.#items.push(null);
  }
  next_item(): Promise<ConversationTailItem | null> {
    if (this.#items.length) {
      return Promise.resolve(this.#items.shift()!);
    }
    if (this.#closed) {
      return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      this.#waiting = resolve;
    });
  }
  async *[Symbol.asyncIterator](): AsyncGenerator<ConversationTailItem> {
    let item: ConversationTailItem | null;
    while ((item = await this.next_item()) !== null) {
      yield item;
    }
  }
}
export class ConversationLiveTail {
  readonly views: ConversationViewLiveOutput;
  readonly #subscriptions = new Map<string, Set<ConversationTailSubscription>>();
  constructor(
    options: {
      view_output_directory?: string;
    } = {},
  ) {
    this.views = new ConversationViewLiveOutput(options.view_output_directory);
  }
  subscribe(conversation_id: string): ConversationTailSubscription {
    const subscription = new ConversationTailSubscription(this, conversation_id);
    let watchers = this.#subscriptions.get(conversation_id);
    if (watchers === undefined) {
      watchers = new Set();
      this.#subscriptions.set(conversation_id, watchers);
    }
    watchers.add(subscription);
    return subscription;
  }
  forget(subscription: ConversationTailSubscription): void {
    const watchers = this.#subscriptions.get(subscription.conversation_id);
    watchers?.delete(subscription);
    if (watchers?.size === 0) {
      this.#subscriptions.delete(subscription.conversation_id);
    }
  }
  publish_event(event: StoredConversationEvent): void {
    this.views.publish_event(event.conversation_id, event.sequence, event.payload);
    for (const watcher of [...(this.#subscriptions.get(event.conversation_id) ?? [])]) {
      watcher.deliver(event);
    }
  }
  publish_frame(conversation_id: string, frame: ConversationLiveTailFrame): void {
    this.views.publish_frame(conversation_id, frame);
    for (const watcher of [...(this.#subscriptions.get(conversation_id) ?? [])]) {
      watcher.deliver(frame);
    }
  }
  close_all_subscriptions(): void {
    for (const watchers of [...this.#subscriptions.values()]) {
      for (const watcher of [...watchers]) {
        watcher.close();
      }
    }
    this.views.close();
  }
  open_subscription_count(): number {
    return [...this.#subscriptions.values()].reduce((total, watchers) => total + watchers.size, 0);
  }
}
