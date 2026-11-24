/** The installer's move mode: install a held server around a copied data folder, or remove it. */
import { isAbsolute } from 'node:path';
import { is_record } from '../../core/records.ts';
import type {
  ServerMoveHeldMarker,
  ServerMoveIngress,
  ServerMoveInstallRequest,
  ServerMoveRemoveRequest,
} from '../../server_move/contracts.ts';
import { validate_server_address } from '../../server_link/logic.ts';
import { release_install_fields, request_root, request_service, request_text } from './home.ts';

function plain_text(value: Record<string, unknown>, name: string): string {
  const item = request_text(value, name);
  if (/[\x00-\x1f\x7f]/u.test(item)) throw new Error(`Invalid move field: ${name}`);
  return item;
}
/** The new server's public HTTPS origin, which trusted ingress repeats as its canonical origin. */
export function https_origin(address: string): string {
  const origin = validate_server_address(address);
  if (!origin.startsWith('https://'))
    throw new Error('The new server address must be an HTTPS origin');
  return origin;
}
function held_marker(value: unknown): ServerMoveHeldMarker {
  if (!is_record(value)) throw new Error('The held marker must be an object');
  const move_number = value.move_number;
  if (typeof move_number !== 'number' || !Number.isSafeInteger(move_number) || move_number < 0)
    throw new Error('The held move number must be a whole number');
  const held = {
    move_id: plain_text(value, 'move_id'),
    target_machine_id: plain_text(value, 'target_machine_id'),
    server_url: plain_text(value, 'server_url'),
    server_id: plain_text(value, 'server_id'),
    move_number,
  };
  https_origin(held.server_url);
  return held;
}
function trusted_ingress(value: unknown): ServerMoveIngress | null {
  if (value === null) return null;
  if (!is_record(value)) throw new Error('trusted_ingress must be an object or null');
  return {
    provider: plain_text(value, 'provider'),
    allowed_login: plain_text(value, 'allowed_login'),
  };
}
export function move_install_request(value: unknown): ServerMoveInstallRequest {
  if (!is_record(value) || value.command !== 'install')
    throw new Error('A move installation request must be an install command object');
  const data_directory = request_text(value, 'data_directory');
  if (!isAbsolute(data_directory) || /[\x00-\x1f\x7f]/u.test(data_directory))
    throw new Error('The staged data folder must be an absolute path');
  return {
    command: 'install',
    ...release_install_fields(value),
    data_directory,
    trusted_ingress: trusted_ingress(value.trusted_ingress),
    held: held_marker(value.held),
  };
}
export function move_remove_request(value: unknown): ServerMoveRemoveRequest {
  if (!is_record(value) || value.command !== 'remove')
    throw new Error('A move removal request must be a remove command object');
  return { command: 'remove', root: request_root(value), service: request_service(value) };
}
/** Fresh settings for the moved server. Settings are not state, so the copy has none. */
export function moved_home_settings(
  request: Pick<ServerMoveInstallRequest, 'trusted_ingress' | 'held'>,
): Record<string, string> {
  if (!request.trusted_ingress) return {};
  return {
    trusted_ingress_provider: request.trusted_ingress.provider,
    trusted_ingress_allowed_login: request.trusted_ingress.allowed_login,
    trusted_ingress_canonical_origin: https_origin(request.held.server_url),
  };
}
/** What a root made by the move mode may hold. A first install also has `machine/`. */
export const MOVED_HOME_ENTRIES: ReadonlySet<string> = new Set([
  'app',
  'data',
  'logs',
  'workspaces',
  'agents',
]);
