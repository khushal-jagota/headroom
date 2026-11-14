/** Live events are bounded and lossy. Durable output remains in the outbox. */
import { encode_bounded } from '../../protocol.ts';
import type { BackendEvent, LiveEventBatch } from '../../wire.generated.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { MAX_ENVELOPE_BYTES, MAX_LIVE_EVENTS_PER_BATCH } from '../configuration.ts';
import type { MacHttpClient } from './http.ts';

export class MacLiveSender {
  private readonly client: Pick<MacHttpClient, 'post_live'>;
  private readonly waiting = new Map<string, BackendEvent[]>();
  private readonly senders = new Map<string, Promise<void>>();
  private readonly cancellation = new AbortController();
  private closed = false;
  constructor(client: Pick<MacHttpClient, 'post_live'>) {
    this.client = client;
  }
  send(event: BackendEvent): void {
    if (this.closed) return;
    const id = event.child.conversation_id;
    let waiting = this.waiting.get(id);
    if (!waiting) {
      waiting = [];
      this.waiting.set(id, waiting);
    }
    waiting.push(event);
    if (waiting.length > MAX_LIVE_EVENTS_PER_BATCH) waiting.shift();
    if (this.senders.has(id)) return;
    const task = Promise.resolve().then(() => this.flush(id));
    this.senders.set(id, task);
  }
  private next_batch(waiting: BackendEvent[]): LiveEventBatch {
    const events: BackendEvent[] = [];
    let size = Buffer.byteLength('{"events":[]}');
    while (waiting.length) {
      let bytes: number;
      try {
        bytes = encode_bounded(waiting[0]!, 'BackendEvent').byteLength + 1;
      } catch {
        waiting.shift();
        continue;
      }
      if (size + bytes > MAX_ENVELOPE_BYTES) {
        if (events.length) break;
        waiting.shift();
        continue;
      }
      size += bytes;
      events.push(waiting.shift()!);
    }
    return { events };
  }
  private async flush(id: string): Promise<void> {
    try {
      if (this.closed) return;
      const waiting = this.waiting.get(id)!;
      while (waiting.length && !this.closed) {
        const batch = this.next_batch(waiting);
        if (!batch.events.length) continue;
        try {
          await this.client.post_live(id, batch, this.cancellation.signal);
        } catch {
          /* Failed live requests are dropped, never replayed as durable output. */
        }
      }
    } finally {
      this.senders.delete(id);
      this.waiting.delete(id);
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.cancellation.abort();
    this.waiting.clear();
    return observe_shutdown(this.settled(), options, 'Mac live requests remain unfinished.');
  }
  async settled(): Promise<void> {
    await Promise.all([...this.senders.values()]);
  }
}
