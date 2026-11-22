import { is_record } from '../../core/records.ts';
import type { HomeInstallHost, HomeInstallRequest, HomeInstallSubmission } from '../contracts.ts';

/** The bootstrap sends this same flat request to the sealed installer. */
export function home_install_request(value: unknown): HomeInstallSubmission {
  if (!is_record(value)) throw new Error('Home installation request must be an object');
  const text = (name: string): string => {
    const item = value[name];
    if (typeof item !== 'string' || !item || item.includes('\0'))
      throw new Error(`Invalid home installation field: ${name}`);
    return item;
  };
  const port = value.port;
  if (!Number.isInteger(port) || typeof port !== 'number' || port < 1 || port > 65535)
    throw new Error('Use a port from 1 through 65535');
  if (typeof value.isolated_providers !== 'boolean')
    throw new Error('isolated_providers must be a boolean');
  const request = {
    root: text('root'),
    service: text('service'),
    port,
    isolated_providers: value.isolated_providers,
    archive: text('archive'),
    archive_sha256: text('archive_sha256'),
    revision: text('revision'),
    update_token: text('update_token'),
  };
  if (/[\x00-\x1f\x7f]/u.test(request.root))
    throw new Error('The installation root must not contain control characters');
  if (/^[A-Za-z0-9][A-Za-z0-9._-]*$/.exec(request.service)?.[0] !== request.service)
    throw new Error('Use a valid service name');
  if (/^[0-9a-f]{40}$/.exec(request.revision)?.[0] !== request.revision)
    throw new Error('The installation requires an exact release revision');
  if (/^[0-9a-f]{64}$/.exec(request.archive_sha256)?.[0] !== request.archive_sha256)
    throw new Error('The installation requires an archive SHA256');
  // The message never repeats the credential.
  if (!/^[\x21-\x7e]+$/.test(request.update_token))
    throw new Error('The update token must be printable text without spaces');
  return request;
}
export function validate_home_host(request: HomeInstallRequest, host: HomeInstallHost): void {
  if (!(
    (host.system === 'linux' && host.architecture === 'x86_64') ||
    (host.system === 'darwin' && host.architecture === 'arm64')
  ))
    throw new Error('Use an Apple Silicon Mac or a Linux x86_64 machine');
  if (host.system === 'darwin' && !/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(request.service))
    throw new Error('Use a valid launchd label');
}
