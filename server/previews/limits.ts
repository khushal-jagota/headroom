import * as config from './config.ts';
import { PreviewError } from './contracts.ts';
export class Capacity {
  readonly active = new Map<string, number>();
  readonly global_limit: number;
  readonly per_preview: number;
  constructor(global_limit: number, per_preview: number) {
    this.global_limit = global_limit;
    this.per_preview = per_preview;
  }
  acquire(generation: string): void {
    if (
      [...this.active.values()].reduce((a, b) => a + b, 0) >= this.global_limit ||
      (this.active.get(generation) ?? 0) >= this.per_preview
    )
      throw new PreviewError('Preview is busy. Try again shortly.', 503);
    this.active.set(generation, (this.active.get(generation) ?? 0) + 1);
  }
  release(generation: string): void {
    const n = this.active.get(generation) ?? 0;
    if (n <= 1) this.active.delete(generation);
    else this.active.set(generation, n - 1);
  }
}
export class TransferBudget {
  private available: number;
  private updated = performance.now();
  private tail: Promise<void> = Promise.resolve();
  readonly rate: number;
  readonly burst: number;
  constructor(
    rate: number = config.TRANSFER_BYTES_PER_SECOND,
    burst: number = config.TRANSFER_BURST_BYTES,
  ) {
    this.rate = rate;
    this.burst = burst;
    this.available = burst;
  }
  async consume(amount: number): Promise<void> {
    const previous = this.tail;
    let release!: () => void;
    this.tail = new Promise<void>((r) => {
      release = r;
    });
    await previous;
    try {
      while (amount > 0) {
        const part = Math.min(amount, this.burst);
        const now = performance.now();
        this.available = Math.min(
          this.burst,
          this.available + ((now - this.updated) * this.rate) / 1000,
        );
        this.updated = now;
        if (this.available >= part) {
          this.available -= part;
          amount -= part;
        } else
          await new Promise((r) => setTimeout(r, ((part - this.available) * 1000) / this.rate));
      }
    } finally {
      release();
    }
  }
}
export class MessageBudget {
  private tokens: number = config.WEBSOCKET_MESSAGE_BURST;
  private updated = performance.now();
  consume(): void {
    const now = performance.now();
    this.tokens = Math.min(
      config.WEBSOCKET_MESSAGE_BURST,
      this.tokens + ((now - this.updated) * config.WEBSOCKET_MESSAGES_PER_SECOND) / 1000,
    );
    this.updated = now;
    if (this.tokens < 1) throw new PreviewError('Preview WebSocket message rate exceeded.', 503);
    this.tokens--;
  }
}
