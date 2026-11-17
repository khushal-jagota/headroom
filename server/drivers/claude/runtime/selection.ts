/** How a running Claude child takes a new model or reasoning effort. */
import type { EffortLevel } from '@anthropic-ai/claude-agent-sdk';
import type { BackendCapabilities } from '../../core/capabilities.ts';
import type { SelectionTransitionInput, SelectionTransitionPlan } from '../../core/selection.ts';

export const CLAUDE_CAPABILITIES: BackendCapabilities = {
  sessions: { supportsModelSwitchInSession: false },
  turns: { supportsActiveSteering: true },
};

// A level the SDK no longer names fails to compile. A new SDK level still restarts.
const LIVE_REASONING_EFFORTS: ReadonlySet<string> = new Set([
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] satisfies readonly EffortLevel[]);
export function is_live_reasoning_effort(value: string): value is EffortLevel {
  return LIVE_REASONING_EFFORTS.has(value);
}
/** Claude takes its model when the child starts, and changes a live effort in the session. */
export function claude_selection_transition(
  input: SelectionTransitionInput,
): SelectionTransitionPlan {
  if (
    input.current.model !== input.target.model &&
    !input.session_capabilities.sessions.supportsModelSwitchInSession
  )
    return { type: 'restart_session' };
  const effort = input.target.reasoning_effort;
  // A live reset returns to the model default, not the user's settings, so clearing restarts.
  if (
    effort !== input.current.reasoning_effort &&
    (effort === null || !is_live_reasoning_effort(effort))
  )
    return { type: 'restart_session' };
  return { type: 'apply_on_next_turn' };
}
