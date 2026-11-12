/** Tool descendants raise their own OOM score without changing their agent's score. */
import { fileURLToPath } from 'node:url';

export const TOOL_SHELL_STARTUP = fileURLToPath(
  new URL('./tool_shell_startup.bash', import.meta.url),
);

export function with_tool_shell_kill_priority(
  environment: Record<string, string>,
): Record<string, string> {
  if (process.platform === 'linux') environment.BASH_ENV = TOOL_SHELL_STARTUP;
  return environment;
}
