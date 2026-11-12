import type { IncomingMessage } from 'node:http';
import type { Config } from '../core/config.ts';
import { PlannerError } from '../core/contracts.ts';
/** The same dedicated gate applies to safe requests and raw upgrade events. */
export function require_preview_ingress(request: IncomingMessage, config: Config): void {
  const headers = new Map<string, string[]>();
  for (let i = 0; i < request.rawHeaders.length; i += 2) {
    const name = request.rawHeaders[i]!.toLowerCase();
    headers.set(name, [...(headers.get(name) ?? []), request.rawHeaders[i + 1]!.trim()]);
  }
  let message: string | undefined;
  if (
    config.trusted_ingress_provider !== 'tailscale' ||
    !config.trusted_ingress_allowed_login ||
    !config.trusted_ingress_canonical_origin?.startsWith('https://')
  )
    message = 'application previews require configured Tailscale HTTPS ingress';
  else if (
    ['tailscale-user-login', 'host', 'origin'].some((name) => (headers.get(name)?.length ?? 0) > 1)
  )
    message = 'duplicate preview security header is not allowed';
  else if (headers.get('tailscale-user-login')?.[0] !== config.trusted_ingress_allowed_login)
    message = 'application previews require the configured Tailscale login';
  else if (headers.get('host')?.[0] !== new URL(config.trusted_ingress_canonical_origin).host)
    message = 'preview host is not the configured public origin';
  else if (
    headers.has('origin') &&
    headers.get('origin')![0] !== config.trusted_ingress_canonical_origin
  )
    message = 'preview origin is not allowed';
  if (message) throw new PlannerError('agent_forbidden', message);
}
