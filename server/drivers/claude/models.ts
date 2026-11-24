/** Pure Claude model, capability, and selection rules for the server and machine code. */
import type { DriverModelRules } from '../core/driver.ts';
import type { EffortLevel } from '@anthropic-ai/claude-agent-sdk';
import type { BackendCapabilities } from '../core/capabilities.ts';
import type { SelectionTransitionInput, SelectionTransitionPlan } from '../core/selection.ts';

const ALIASES = ['opus', 'sonnet', 'haiku', 'fable'];
const VERSION = /^claude-([a-z][a-z0-9]*)-(\d+(?:[-.]\d+)*)(\[1m\])?$/;
/** The bare aliases a resolved id can still be, named as they are. */
const NAMED_ALIAS = /^(opus|sonnet|haiku)(\[1m\])?$/i;
const capitalised = (word: string) => word[0].toUpperCase() + word.slice(1);

function variant(model_id: string): string | null {
  for (const alias of ALIASES)
    if (model_id === alias || model_id === alias + '[1m]') return model_id;
  const match = VERSION.exec(model_id);
  return match && match[0] === model_id ? match[1] + (match[3] || '') : null;
}
export const claude_models: DriverModelRules = {
  variant,
  version(model_id) {
    const match = VERSION.exec(model_id);
    if (!match || match[0] !== model_id) return [];
    // A date part (eight digits) is not a version part.
    return match[2]
      .split(/[-.]/)
      .filter((part) => part.length !== 8)
      .map(Number);
  },
  is_offered(requested, offered) {
    if (offered.includes(requested)) return true;
    const base_of = (model: string) => (model.endsWith('[1m]') ? model.slice(0, -4) : model);
    const base = base_of(requested);
    const listed_bases = new Set(offered.map(base_of));
    if (listed_bases.has(base)) return true;
    if (!ALIASES.includes(base)) return false;
    return [...listed_bases].some((listed) => variant(listed) === base);
  },
  /** As Claude's versioned display name: claude-opus-5-5 is Opus 5.5. Dates drop; [1m] is (1M). */
  readable_name(model_id) {
    const large_context = model_id.endsWith('[1m]') ? ' (1M)' : '';
    const base = large_context ? model_id.slice(0, -4) : model_id;
    const [provider, family, ...parts] = base.split('-');
    if (provider === 'claude' && family) {
      const version = parts.filter((part) => !/^\d{8}$/.test(part));
      if (version.every((part) => /^\d+$/.test(part)))
        return (
          [capitalised(family), ...(version.length ? [version.join('.')] : [])].join(' ') +
          large_context
        );
    }
    const alias = NAMED_ALIAS.exec(model_id);
    return alias ? capitalised(alias[1]) + large_context : null;
  },
};

/** How a running Claude child takes a new model or reasoning effort. */

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
