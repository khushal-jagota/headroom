/** Adapted from T3 Code b79d277cddaf65858418f29d73b87fcc3d1a19c5
 * (packages/provider-core/src/server/selectionTransition.ts), without create_with_handoff.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/** How a running child takes a new model or reasoning effort. */
import type { BackendCapabilities } from './capabilities.ts';

export interface BackendSelection {
  readonly model: string | null;
  readonly reasoning_effort: string | null;
}
export interface SelectionTransitionInput {
  readonly current: BackendSelection;
  readonly target: BackendSelection;
  readonly session_capabilities: BackendCapabilities;
}
export type SelectionTransitionPlan =
  | { readonly type: 'apply_on_next_turn' }
  | { readonly type: 'restart_session' }
  | { readonly type: 'reject'; readonly reason: string };

/** Every turn names its own selection, so any change applies to the next turn. */
export function turn_scoped_selection_transition(): SelectionTransitionPlan {
  return { type: 'apply_on_next_turn' };
}
export function acp_selection_transition(input: SelectionTransitionInput): SelectionTransitionPlan {
  if (
    input.current.model !== input.target.model &&
    !input.session_capabilities.sessions.supportsModelSwitchInSession
  ) {
    return {
      type: 'reject',
      reason: 'The active ACP session does not expose a model-switch capability.',
    };
  }
  return { type: 'apply_on_next_turn' };
}
