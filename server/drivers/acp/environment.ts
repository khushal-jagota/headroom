/** An ACP child inherits a short allowlist, then its agent's values, then conversation identity. */
import {
  LAUNCH_TOKEN_ENVIRONMENT_NAME,
  UNIDENTIFIED_LAUNCH_TOKEN,
} from '../../agent_identity/contracts.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';

/** Nothing else of the server's environment, credentials included, reaches an ACP agent. */
export const INHERITED_ENVIRONMENT_NAMES = [
  'HOME',
  'LOGNAME',
  'PATH',
  'SHELL',
  'TERM',
  'USER',
] as const;
export function acp_child_environment(
  overrides: readonly (readonly [string, string])[],
  resolved_start: Pick<ResolvedConversationStart, 'role_materials'>,
  inherited: Readonly<NodeJS.ProcessEnv> = process.env,
): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const name of INHERITED_ENVIRONMENT_NAMES) {
    const value = inherited[name];
    if (value !== undefined) environment[name] = value;
  }
  Object.assign(environment, Object.fromEntries(overrides));
  environment[LAUNCH_TOKEN_ENVIRONMENT_NAME] = UNIDENTIFIED_LAUNCH_TOKEN;
  Object.assign(
    environment,
    Object.fromEntries(resolved_start.role_materials?.identity_environment_variables ?? []),
  );
  return environment;
}
