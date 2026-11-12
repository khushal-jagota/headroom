/** Registry and transport boundary for application previews. */
export class PreviewError extends Error {
  readonly status_code: number;
  constructor(message: string, status_code = 409) {
    super(message);
    this.status_code = status_code;
  }
}
export interface ProcessIdentity {
  boot_id: string;
  pid: number;
  start_ticks: number;
  listener_inode: number;
  port: number;
  worktree: string;
}
export interface PreviewRecord {
  ticket_id: string;
  generation: string;
  worktree: string;
  public_origin: string;
  server_epoch: string;
  created_at: number;
  state: 'prepared' | 'active' | 'ended';
  ended_reason: string | null;
  process: ProcessIdentity | null;
}
export interface TicketEligibility {
  ticket_id: string;
  project_folder: string | null;
  terminal: boolean;
  locale: 'vps' | 'macos';
}
export const base_path = (record: PreviewRecord): string =>
  `/dev/tickets/${record.ticket_id}/${record.generation}/`;
export function port(record: PreviewRecord): number {
  if (!record.process) throw new PreviewError('Preview application is not registered.', 410);
  return record.process.port;
}
export type Header = [string, string];
