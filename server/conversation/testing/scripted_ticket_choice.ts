/** One selected secondary preference write fails before the real writer is called. */
import type { ConversationCoreOptions } from '../runtime/contracts.ts';

type HeldChoice = NonNullable<ConversationCoreOptions['on_held_model_change']>;

export class ScriptedTicketChoiceFault {
  private slot: {
    id: string;
    pending: boolean;
    injected_failures: number;
    error: Error;
  } | null = null;

  wrap(callback: HeldChoice): HeldChoice {
    return (...values: Parameters<HeldChoice>) => {
      const slot = this.slot;
      if (slot?.id === values[0] && slot.pending) {
        slot.pending = false;
        slot.injected_failures++;
        throw slot.error;
      }
      return callback(...values);
    };
  }

  arm(id: string) {
    if (this.slot) throw new Error('A Ticket choice failure is already armed');
    this.slot = {
      id,
      pending: true,
      injected_failures: 0,
      error: new Error('Ticket model write unavailable'),
    };
    return { armed: true };
  }

  observe(id: string) {
    if (this.slot?.id !== id) throw new Error('No matching Ticket choice failure');
    return {
      pending: this.slot.pending,
      injected_failures: this.slot.injected_failures,
      detail: this.slot.error.message,
    };
  }

  close(): void {
    this.slot = null;
  }
}
