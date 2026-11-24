/** What Antigravity's `agy acp` tells the shared ACP backend child. */
import { RequestError } from '@agentclientprotocol/sdk/unstable';
import { ConversationAccess } from '../../../conversation/contracts.ts';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import type { AcpFlavor, AcpLaunch } from '../../acp/flavor.ts';
import { acp_child_environment } from '../../acp/environment.ts';
import { antigravity_driver } from '../driver.ts';
import { PROMPT_INACTIVITY_TIMEOUT_MS } from './configuration.ts';
import { watch_antigravity_prompt_outcome } from './turn_outcome.ts';
import { antigravity_background_task_description } from './background_work.ts';

export const ANTIGRAVITY_SIGN_IN_REQUIRED =
  'Antigravity is not signed in. Sign in to Antigravity from Panels.';
/** ACP's auth_required: a signed-out agy answers `session/new` with it. */
const AUTH_REQUIRED_CODE = -32000;

export interface AntigravityLaunchOptions {
  readonly executable: string;
  /** Absolute. */
  readonly home: string;
  readonly worker_process_root?: string;
  readonly panels_server_url: string;
  readonly environment?: Readonly<NodeJS.ProcessEnv>;
}
/**
 * The launch every `agy acp` Panels starts shares: conversations, the model catalog, sign-in and
 * sign-out alike. It uses agy's default app data directory, so it runs on the CLI's own login.
 */
export function antigravity_acp_launch(options: AntigravityLaunchOptions): AcpLaunch {
  return {
    argv: [options.executable, 'acp', '--gemini-dir', options.home, '--disable-telemetry'],
    environment_overrides: [
      ['GEMINI_HOME', options.home],
      // No browser can open here.
      ['BROWSER', 'false'],
      ['PLAN_SERVER_URL', options.panels_server_url],
      ...(options.worker_process_root === undefined
        ? []
        : [worker_process_root_environment(options.worker_process_root)]),
    ],
    ...(options.environment === undefined ? {} : { inherited_environment: options.environment }),
  };
}
export function antigravity_requires_sign_in(error: unknown): string | null {
  return error instanceof RequestError && error.code === AUTH_REQUIRED_CODE
    ? ANTIGRAVITY_SIGN_IN_REQUIRED
    : null;
}
/** `one_shot_children` runs the `agy --version` floor check when no verdict is kept for the file. */
export function create_antigravity_flavor(
  options: AntigravityLaunchOptions,
  one_shot_children: OwnedOneShotChildren,
): AcpFlavor {
  const launch = antigravity_acp_launch(options);
  return {
    label: 'Antigravity',
    background_task_description: antigravity_background_task_description,
    async launch(start) {
      // As Codex and Claude do before each launch. The card's read normally left a verdict.
      await ensure_cli_compatible(antigravity_driver.driver_kind, options.executable, {
        children: one_shot_children,
        ...(options.environment === undefined
          ? {}
          : {
              environment: acp_child_environment(
                launch.environment_overrides,
                { role_materials: null },
                options.environment,
              ),
            }),
      });
      return start.access === ConversationAccess.full
        ? { ...launch, argv: [...launch.argv, '--dangerously-skip-permissions'] }
        : launch;
    },
    requires_sign_in: antigravity_requires_sign_in,
    prompt_inactivity_timeout_ms: PROMPT_INACTIVITY_TIMEOUT_MS,
    watch_prompt_outcome: (session_id) =>
      watch_antigravity_prompt_outcome(options.home, session_id),
  };
}
