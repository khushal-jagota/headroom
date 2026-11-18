/** Hermes inherits ACP's allowlist, explicit launch values, then conversation identity. */
import { fileURLToPath } from 'node:url';
import { posix } from 'node:path';
import { ConversationAccess } from '../../../conversation/contracts.ts';
import type { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import { acp_child_environment } from '../../acp/environment.ts';
import type { AcpLaunch } from '../../acp/flavor.ts';

export type AcpChildLaunch = AcpLaunch;
/** Interpreter parents ignore '.' components but retain '..' for filesystem resolution. */
export function hermes_source_root(path: string): string {
  const parents: string[] = [];
  let current =
    path
      .split('/')
      .filter((part) => part !== '.')
      .join('/') || '.';
  while (parents.length < 3) {
    const parent = current === '//' ? '//' : posix.dirname(current);
    if (parent === current) break;
    parents.push(parent);
    current = parent;
  }
  return parents[2] ?? parents[0] ?? current;
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
export function child_environment(
  launch: AcpChildLaunch,
  resolved_start: Pick<ResolvedConversationStart, 'access' | 'role_materials'>,
  inherited: Readonly<NodeJS.ProcessEnv> = process.env,
): Record<string, string> {
  const full_access: readonly (readonly [string, string])[] =
    resolved_start.access === ConversationAccess.full ? [['HERMES_YOLO_MODE', '1']] : [];
  return acp_child_environment(
    [...launch.environment_overrides, ...full_access],
    resolved_start,
    inherited,
  );
}
