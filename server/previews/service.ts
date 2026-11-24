/** The server owns preview routes and relays. Machine code owns native inspection. */
import { RemotePreviews } from './remote.ts';
import type { Database } from '../core/database/contracts.ts';
import { PreviewError, type PreviewRecord } from './contracts.ts';

export class PreviewService {
  readonly remote: RemotePreviews;
  private rechecks_sealed = false;
  constructor(database: Database, public_origin: string) {
    this.remote = new RemotePreviews(database, public_origin);
  }
  async start(): Promise<void> {}
  async prepare(_ticket_id: string): Promise<PreviewRecord> {
    throw this.machine_command_required();
  }
  async register(_ticket_id: string, _port: number): Promise<PreviewRecord> {
    throw this.machine_command_required();
  }
  async show(_ticket_id: string): Promise<PreviewRecord | null> {
    throw this.machine_command_required();
  }
  async stop(_ticket_id: string): Promise<PreviewRecord | null> {
    throw this.machine_command_required();
  }
  private machine_command_required(): PreviewError {
    return new PreviewError('Preview commands must run through the Ticket machine code.');
  }
  async check(ticket_id: string, generation: string): Promise<PreviewRecord> {
    return this.remote.check(ticket_id, generation);
  }
  async recheck_all(): Promise<string[]> {
    return this.rechecks_sealed ? [] : this.remote.recheck();
  }
  seal_rechecks(): void {
    this.rechecks_sealed = true;
  }
  seal(): void {
    this.remote.close();
    this.seal_rechecks();
  }
  async settled(): Promise<void> {}
}
