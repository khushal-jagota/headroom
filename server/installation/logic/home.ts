import { is_record } from '../../core/records.ts';
import { timezone } from '../../measures/logic/dates.ts';
import type { HomeInstallHost, HomeInstallRequest, HomeInstallSubmission } from '../contracts.ts';

/** One required request text field: present, non-empty, and without NUL. */
export function request_text(value: Record<string, unknown>, name: string): string {
  const item = value[name];
  if (typeof item !== 'string' || !item || item.includes('\0'))
    throw new Error(`Invalid home installation field: ${name}`);
  return item;
}
export function request_root(value: Record<string, unknown>): string {
  const root = request_text(value, 'root');
  if (/[\x00-\x1f\x7f]/u.test(root))
    throw new Error('The installation root must not contain control characters');
  return root;
}
export function request_service(value: Record<string, unknown>): string {
  const service = request_text(value, 'service');
  if (/^[A-Za-z0-9][A-Za-z0-9._-]*$/.exec(service)?.[0] !== service)
    throw new Error('Use a valid service name');
  return service;
}
/** The fields of a sealed release installation that the first install and a move share. */
export function release_install_fields(
  value: Record<string, unknown>,
): Omit<HomeInstallRequest, 'isolated_providers' | 'skip_command_link'> {
  const port = value.port;
  if (!Number.isInteger(port) || typeof port !== 'number' || port < 1 || port > 65535)
    throw new Error('Use a port from 1 through 65535');
  const request = {
    root: request_root(value),
    service: request_service(value),
    port,
    archive: request_text(value, 'archive'),
    archive_sha256: request_text(value, 'archive_sha256'),
    revision: request_text(value, 'revision'),
  };
  if (/^[0-9a-f]{40}$/.exec(request.revision)?.[0] !== request.revision)
    throw new Error('The installation requires an exact release revision');
  if (/^[0-9a-f]{64}$/.exec(request.archive_sha256)?.[0] !== request.archive_sha256)
    throw new Error('The installation requires an archive SHA256');
  return request;
}
/** The bootstrap sends this same flat request to the sealed installer. */
export function home_install_request(value: unknown): HomeInstallSubmission {
  if (!is_record(value)) throw new Error('Home installation request must be an object');
  if (typeof value.isolated_providers !== 'boolean')
    throw new Error('isolated_providers must be a boolean');
  if (value.skip_command_link !== undefined && typeof value.skip_command_link !== 'boolean')
    throw new Error('skip_command_link must be a boolean');
  const owner_time_zone = timezone(
    value.owner_time_zone === undefined
      ? Intl.DateTimeFormat().resolvedOptions().timeZone
      : request_text(value, 'owner_time_zone'),
  );
  const request = {
    ...release_install_fields(value),
    owner_time_zone,
    skip_command_link: value.skip_command_link ?? false,
    isolated_providers: value.isolated_providers,
  };
  if (value.update_token === undefined) return request;
  // Earlier bootstraps supplied a credential. The message never repeats it.
  const update_token = request_text(value, 'update_token');
  if (!/^[\x21-\x7e]+$/.test(update_token))
    throw new Error('The update token must be printable text without spaces');
  return { ...request, update_token };
}
export function validate_home_host(
  request: Pick<HomeInstallRequest, 'service'>,
  host: HomeInstallHost,
): void {
  if (!(
    (host.system === 'linux' && host.architecture === 'x86_64') ||
    (host.system === 'darwin' && host.architecture === 'arm64')
  ))
    throw new Error('Use an Apple Silicon Mac or a Linux x86_64 machine');
  if (host.system === 'darwin' && !/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(request.service))
    throw new Error('Use a valid launchd label');
}
