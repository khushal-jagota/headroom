/** Shared callback state and admission facts. Event and lifecycle owners decide transitions. */
import type { AskReference, ChildReference } from '../../wire.generated.ts';
import type { MacChildStateContract } from './command_contracts.ts';

function reference_key(child: ChildReference): string {
  return JSON.stringify([child.executor_incarnation, child.child_id, child.conversation_id]);
}

export class MacChildState implements MacChildStateContract {
  private stopped = false;
  private readonly retiring = new Set<string>();
  private readonly retired = new Set<string>();
  private readonly asks = new Map<string, Map<string, AskReference>>();

  get admission_stopped(): boolean {
    return this.stopped;
  }
  seal_admission(): void {
    this.stopped = true;
  }
  resume_admission(): void {
    this.stopped = false;
  }
  begin_retirement(child_id: string): void {
    this.retiring.add(child_id);
  }
  is_retiring(child_id: string): boolean {
    return this.retiring.has(child_id);
  }
  is_retired(child_id: string): boolean {
    return this.retired.has(child_id);
  }
  mark_retired(child: ChildReference): void {
    this.retiring.add(child.child_id);
    this.retired.add(child.child_id);
    this.clear(child);
  }
  install(ask: AskReference): void {
    const key = reference_key(ask.child);
    let current = this.asks.get(key);
    if (!current) {
      current = new Map();
      this.asks.set(key, current);
    }
    current.set(ask.ask_id, ask);
  }
  get(child: ChildReference, ask_id: string): AskReference | null {
    return this.asks.get(reference_key(child))?.get(ask_id) ?? null;
  }
  take(child: ChildReference, ask_id: string): AskReference | null {
    const key = reference_key(child);
    const current = this.asks.get(key);
    const ask = current?.get(ask_id) ?? null;
    if (current) {
      current.delete(ask_id);
      if (current.size === 0) this.asks.delete(key);
    }
    return ask;
  }
  clear(child: ChildReference): void {
    this.asks.delete(reference_key(child));
  }
  entries(child: ChildReference): readonly AskReference[] {
    return [...(this.asks.get(reference_key(child))?.values() ?? [])];
  }
  count(child?: ChildReference): number {
    if (child !== undefined) return this.asks.get(reference_key(child))?.size ?? 0;
    let count = 0;
    for (const current of this.asks.values()) count += current.size;
    return count;
  }
}
