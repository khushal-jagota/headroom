/** One-time browser return addresses never enter a durable mailbox or journal. */
import { randomUUID } from 'node:crypto';
import { HttpError } from '../core/http/errors.ts';
export class BackendControlSecrets {
  private readonly values = new Map<string, { machine_id: string; value: string }>();
  keep(machine_id: string, value: string): string {
    const id = randomUUID();
    this.values.set(id, { machine_id, value });
    return id;
  }
  discard(id: string): void {
    this.values.delete(id);
  }
  take(machine_id: string, id: string): string {
    const entry = this.values.get(id);
    if (entry === undefined || entry.machine_id !== machine_id)
      throw new HttpError(410, 'That sign-in return address is no longer available.');
    this.values.delete(id);
    return entry.value;
  }
}
