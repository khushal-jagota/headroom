/** What Antigravity's `agy acp` tells the shared ACP backend child. */
import { RequestError } from '@agentclientprotocol/sdk/unstable';
import { ConversationAccess } from '../../../conversation/contracts.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import type { AcpFlavor, AcpLaunch } from '../../acp/flavor.ts';
import { APP_DATA_DIRECTORY_NAME, PROMPT_INACTIVITY_TIMEOUT_MS } from './configuration.ts';
import { watch_antigravity_prompt_outcome } from './turn_outcome.ts';

export const ANTIGRAVITY_SIGN_IN_REQUIRED =
  'Antigravity is not signed in. Sign in to Antigravity from Panels.';
/** ACP's auth_required: a signed-out agy answers `session/new` with it. */
const AUTH_REQUIRED_CODE = -32000;

export interface AntigravityLaunchOptions {
  readonly executable: string;
  /** Absolute. */
  readonly home: string;
  readonly worker_process_root: string;
  readonly panels_server_url: string;
}
/** The launch every `agy acp` Panels starts shares: conversations and the model catalog alike. */
export function antigravity_acp_launch(options: AntigravityLaunchOptions): AcpLaunch {
  return {
    argv: [
      options.executable,
      'acp',
      '--gemini-dir',
      options.home,
      '--app-data-dir',
      APP_DATA_DIRECTORY_NAME,
      '--disable-telemetry',
    ],
    environment_overrides: [
      ['GEMINI_HOME', options.home],
      // Panels' sign-in token is a file under the app data directory, not the system keyring.
      ['AGY_ACP_FORCE_FILE_STORAGE', '1'],
      // No browser can open here.
      ['BROWSER', 'false'],
      ['PLAN_SERVER_URL', options.panels_server_url],
      worker_process_root_environment(options.worker_process_root),
    ],
  };
}
export function antigravity_requires_sign_in(error: unknown): string | null {
  return error instanceof RequestError && error.code === AUTH_REQUIRED_CODE
    ? ANTIGRAVITY_SIGN_IN_REQUIRED
    : null;
}
export function create_antigravity_flavor(options: AntigravityLaunchOptions): AcpFlavor {
  const launch = antigravity_acp_launch(options);
  return {
    label: 'Antigravity',
    launch: (start) =>
      start.access === ConversationAccess.full
        ? { ...launch, argv: [...launch.argv, '--dangerously-skip-permissions'] }
        : launch,
    requires_sign_in: antigravity_requires_sign_in,
    prompt_inactivity_timeout_ms: PROMPT_INACTIVITY_TIMEOUT_MS,
    watch_prompt_outcome: (session_id) =>
      watch_antigravity_prompt_outcome(options.home, session_id),
  };
}
