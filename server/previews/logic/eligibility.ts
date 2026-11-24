import { PreviewError } from '../contracts.ts';
export function require_port(port: number, panels_port: number): void {
  if (port < 1024 || port > 65535 || [8767, panels_port].includes(port))
    throw new PreviewError('Choose a non-privileged application port distinct from Panels.');
}
