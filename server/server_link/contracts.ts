/** Server authority and machine memory are independent of the host operating system. */
export type { ServerLocation } from '../executor/wire.generated.ts';
export interface MachineServerRecord {
  readonly server_url: string;
  readonly server_id: string | null;
  readonly highest_move_number: number;
}
export const SERVER_LOCATION_HEADER = 'x-panels-server-location';
export const SERVER_ID_HEADER = 'x-panels-server-id';
export const MOVE_NUMBER_HEADER = 'x-panels-move-number';
export const SERVER_LOCATION_PATH = '/api/server-link';
/** The HTTP owner supplies an unauthenticated, bounded probe on its retained session. */
export interface ServerLinkAdmission {
  readonly current_url: string;
  current(): MachineServerRecord;
  needs_probe(): boolean;
  request_failed(): void;
  prepare(probe: (origin: string) => Promise<unknown>): Promise<MachineServerRecord>;
  verify(header: string | string[] | undefined, origin: string): void;
  accept(
    header: string | string[] | undefined,
    origin: string,
    probe: (origin: string) => Promise<unknown>,
  ): Promise<void>;
}
