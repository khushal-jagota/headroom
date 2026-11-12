/** Hermes inherits ACP's allowlist, explicit launch values, then conversation identity. */
import { fileURLToPath } from 'node:url';
import { ConversationAccess } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';

export interface AcpChildLaunch {
  readonly argv: readonly string[];
  readonly environment_overrides: readonly (readonly [string, string])[];
}
export function hermes_acp_child_launch(options: {
  hermes_python: string;
  hermes_home: string;
  hermes_python_source_root: string;
  panels_server_url: string;
}): AcpChildLaunch {
  return {
    argv: [
      options.hermes_python,
      fileURLToPath(
        new URL(
          '../../../../src/planner/conversation/backends/hermes_acp_extension.py',
          import.meta.url,
        ),
      ),
    ],
    environment_overrides: [
      ['HERMES_HOME', options.hermes_home],
      ['HERMES_PYTHON_SRC_ROOT', options.hermes_python_source_root],
      ['PLAN_SERVER_URL', options.panels_server_url],
    ],
  };
}
export const INHERITED_ENVIRONMENT_NAMES = [
  'HOME',
  'LOGNAME',
  'PATH',
  'SHELL',
  'TERM',
  'USER',
] as const;
export function child_environment(
  launch: AcpChildLaunch,
  resolved_start: Pick<ResolvedConversationStart, 'access' | 'role_materials'>,
  inherited: Readonly<NodeJS.ProcessEnv> = process.env,
): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const name of INHERITED_ENVIRONMENT_NAMES) {
    const value = inherited[name];
    if (value !== undefined) environment[name] = value;
  }
  Object.assign(environment, Object.fromEntries(launch.environment_overrides));
  if (resolved_start.access === ConversationAccess.full) environment.HERMES_YOLO_MODE = '1';
  Object.assign(
    environment,
    Object.fromEntries(resolved_start.role_materials?.identity_environment_variables ?? []),
  );
  return environment;
}
