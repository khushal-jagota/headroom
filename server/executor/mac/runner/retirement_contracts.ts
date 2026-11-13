/** Source custody and exact command retirement are shared with the event host. */
import type { ChildCommand, ChildReference, CommandOutcome } from '../../wire.generated.ts';
import type { MacChildStateContract, MacCommandWork } from './command_contracts.ts';
import type { MacNativeChildrenContract } from './contracts.ts';

export interface MacSourceWorkContract {
  source_work<T>(
    reference: ChildReference,
    work: () => Promise<T>,
    options?: { final_event?: boolean },
  ): Promise<T>;
  /** Join actual work. Failure history remains visible separately. */
  finish(reference?: ChildReference): Promise<void>;
  failed(reference?: ChildReference): boolean;
  count(reference?: ChildReference): number;
  errors(): readonly unknown[];
  /** Permanent shutdown seals after producers and admitted work join. */
  seal(): void;
}
export interface MacRetirementDependencies {
  readonly native_children: MacNativeChildrenContract;
  readonly state: MacChildStateContract;
  readonly sources: MacSourceWorkContract;
}
export interface MacRetirementContract {
  stop_exact(
    command: ChildCommand,
    work: MacCommandWork,
    invoking: () => void,
  ): Promise<CommandOutcome>;
}
