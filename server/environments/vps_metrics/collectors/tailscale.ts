/** One bounded read-only tailscale command, run as the server's own user. */
import { bounded_command, launcher } from './command.ts';
import type { LaunchCommand } from './command.ts';
export function query_tailscale(
  args: readonly string[],
  timeout: number,
  output_bytes: number,
  execute: LaunchCommand = launcher('tailscale'),
): Promise<string> {
  return bounded_command(execute, args, timeout, output_bytes, 'tailscale query');
}
