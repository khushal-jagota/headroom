/** What one ACP agent tells the shared ACP backend child. A Panels-sized subset of T3's flavor. */
import type { SessionNotification } from '@agentclientprotocol/sdk/unstable';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import type { BackendCapabilities } from '../core/capabilities.ts';

export { ACP_CHILD_CAPABILITIES } from '../core/capabilities.ts';
/** The agent's command and its own environment values. The base adds the shared rest. */
export interface AcpLaunch {
  readonly argv: readonly string[];
  readonly environment_overrides: readonly (readonly [string, string])[];
  /** Inherited host environment filtered through INHERITED_ENVIRONMENT_NAMES. Defaults to process.env. */
  readonly inherited_environment?: Readonly<NodeJS.ProcessEnv>;
}
/** Taken before a prompt is written. Read once the agent has answered that prompt `end_turn`. */
export interface AcpPromptOutcomeWatch {
  /** The failure the agent reported only outside the ACP stream, or null. */
  failure(): Promise<string | null>;
}
export interface AcpFlavor {
  /** The agent's display name, used in the base's own error text. */
  readonly label: string;
  /** A candidate async tool. It becomes background work only if it outlives its prompt. */
  background_task_description?(update: SessionNotification['update']): string | null;
  /**
   * Called once for each child start, before anything is spawned. It may check the agent first,
   * such as its CLI floor; a throw fails the start with `BackendSpawnFailed`.
   */
  launch(start: ResolvedConversationStart): AcpLaunch | Promise<AcpLaunch>;
  /**
   * The signed-out text when this agent error means the agent needs a sign-in, else null. A
   * start that fails this way does not start; a prompt that fails this way fails its turn with
   * this text.
   */
  requires_sign_in?(error: unknown): string | null;
  /** A turn whose agent sends nothing for this long fails, and its child is not reused. */
  readonly prompt_inactivity_timeout_ms?: number;
  /** Corrects a turn that the agent ended `end_turn` from a source outside the ACP stream. */
  watch_prompt_outcome?(session_id: string): AcpPromptOutcomeWatch;
}
