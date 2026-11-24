import type { MachineServerRecord, ServerLocation } from './contracts.ts';
import { posix } from 'node:path';
import { ExecutorValidationError } from '../executor/protocol_errors.ts';

/**
 * The local executor link address: an absolute path to the server's socket file on this
 * machine (`executor_link_socket_path` names it). Null when the value is not this form; a
 * refusal when it is this form but not a plain path. A Windows named pipe would be a second
 * form here.
 */
export function local_server_address(value: string): string | null {
  if (!value.startsWith('/')) return null;
  if (
    /[\u0000-\u001f\u007f]/u.test(value) ||
    posix.normalize(value) !== value ||
    value.endsWith('/')
  )
    throw new ExecutorValidationError('The local server link must be a plain absolute path.');
  return value;
}
/** An HTTPS origin, or the local link's socket path on the server's own machine. */
export function validate_server_address(value: string): string {
  const local = local_server_address(value);
  if (local !== null) return local;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ExecutorValidationError(
      'The server address must be an HTTPS origin or a socket path.',
    );
  }
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    /\s/u.test(value)
  )
    throw new ExecutorValidationError(
      'The server address must be an HTTPS origin or a socket path.',
    );
  return url.origin;
}
export function parse_server_location(value: unknown): ServerLocation {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new ExecutorValidationError('The server location is invalid.');
  const item = value as Record<string, unknown>;
  if (
    typeof item.server_id !== 'string' ||
    !item.server_id.trim() ||
    !Number.isSafeInteger(item.move_number) ||
    (item.move_number as number) < 0 ||
    !['active', 'moved'].includes(item.state as string) ||
    !(item.server_url === null || typeof item.server_url === 'string')
  )
    throw new ExecutorValidationError('The server location is invalid.');
  const server_url =
    item.server_url === null ? null : validate_server_address(item.server_url as string);
  if (item.state === 'moved' && server_url === null)
    throw new ExecutorValidationError('A moved server must give its new address.');
  return {
    server_id: item.server_id,
    move_number: item.move_number as number,
    server_url,
    state: item.state as ServerLocation['state'],
  };
}
export function accept_server_location(
  record: MachineServerRecord,
  location: ServerLocation,
  address: string,
): MachineServerRecord {
  if (record.server_id !== null && record.server_id !== location.server_id)
    throw new ExecutorValidationError('This address belongs to another server.');
  if (location.move_number < record.highest_move_number)
    throw new ExecutorValidationError('This server has an older move number.');
  return {
    server_id: location.server_id,
    highest_move_number: location.move_number,
    server_url: location.server_url ?? validate_server_address(address),
  };
}
/**
 * A machine follows an announced address when the server moved. Machine code on the server's
 * own machine keeps its local link while the server is active, because the announced address
 * is for the other machines.
 */
export function follows_announced_address(location: ServerLocation, origin: string): boolean {
  if (location.server_url === null || location.server_url === origin) return false;
  return location.state === 'moved' || local_server_address(origin) === null;
}
