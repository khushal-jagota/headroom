/** T3 selectionTransition.ts shapes, less create_with_handoff. The core uses them in a later stage. */
import type { BackendCapabilities } from './capabilities.ts';

export interface BackendSelection {
  readonly model: string | null;
  readonly reasoning_effort: string | null;
  readonly reasoning_effort_is_set: boolean;
}
export interface SelectionTransitionInput {
  /** What the running child was started or last set with. */
  readonly current: BackendSelection;
  readonly target: BackendSelection;
  readonly session_capabilities: BackendCapabilities;
}
export type SelectionTransitionPlan =
  | { readonly type: 'apply_on_next_turn' }
  | { readonly type: 'restart_session' }
  | { readonly type: 'reject'; readonly reason: string };
