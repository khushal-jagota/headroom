/** Retain admitted source promises independently of the caller that observes them. */
import { same_reference } from '../../child_reference.ts';
import type { ChildReference } from '../../wire.generated.ts';
import type { MacChildStateContract } from './command_contracts.ts';
import type { MacSourceWorkContract } from './retirement_contracts.ts';

export class MacSourceWork implements MacSourceWorkContract {
  private readonly state: MacChildStateContract;
  private readonly stopping: () => boolean;
  private readonly active = new Map<Promise<unknown>, ChildReference>();
  private readonly failures: { reference: ChildReference; error: unknown }[] = [];
  private sealed = false;
  constructor(state: MacChildStateContract, stopping: () => boolean) {
    this.state = state;
    this.stopping = stopping;
  }
  source_work<T>(
    reference: ChildReference,
    work: () => Promise<T>,
    options: { final_event?: boolean } = {},
  ): Promise<T> {
    if (
      this.sealed ||
      this.state.is_retired(reference.child_id) ||
      ((this.stopping() || this.state.is_retiring(reference.child_id)) && !options.final_event)
    )
      return Promise.reject(
        Object.assign(new Error('source child is retired or retiring'), {
          name: 'PermissionError',
        }),
      );
    // Reserve custody before the callback can emit more work or reenter shutdown.
    const task = Promise.resolve().then(work);
    this.active.set(task, reference);
    void task.then(
      () => this.active.delete(task),
      (error) => {
        this.failures.push({ reference, error });
        this.active.delete(task);
      },
    );
    return task;
  }
  async finish(reference?: ChildReference): Promise<void> {
    while (true) {
      const tasks = [...this.active]
        .filter(([, child]) => reference === undefined || same_reference(child, reference))
        .map(([task]) => task);
      if (tasks.length === 0) return;
      await Promise.allSettled(tasks);
    }
  }
  failed(reference?: ChildReference): boolean {
    return this.failures.some(
      (failure) => reference === undefined || same_reference(failure.reference, reference),
    );
  }
  count(reference?: ChildReference): number {
    return [...this.active.values()].filter(
      (child) => reference === undefined || same_reference(child, reference),
    ).length;
  }
  errors(): readonly unknown[] {
    return this.failures.map((failure) => failure.error);
  }
  seal(): void {
    this.sealed = true;
  }
}
