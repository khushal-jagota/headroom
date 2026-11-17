/**
 * The shared scenario matrix. Each scenario proves something the others do not.
 *
 * Steps are shared by every driver. Prompt texts carry the scripted Claude CLI's `[CASE:…]`
 * tags, so the scripted peer that Claude transcripts were captured from takes the same steps.
 */
import type { PromptDeliveryMode } from '../../../conversation/contracts.ts';
import type { ConversationTurnEnding } from '../../../conversation/events.ts';

export type ReplayStep =
  | { readonly step: 'write_prompt'; readonly text: string; readonly mode?: PromptDeliveryMode }
  | { readonly step: 'steer'; readonly text: string }
  | { readonly step: 'cancel_running_turn' }
  /** Answer the latest raised ask with its first option of this kind. */
  | { readonly step: 'answer_permission_ask'; readonly option_kind: string }
  /** Wait for the next event of this class that no earlier step waited for. */
  | { readonly step: 'await_event'; readonly event_class: string }
  | { readonly step: 'stop' };

export const SCENARIO_NAMES = [
  'two_turns',
  'tool_call',
  'interrupt',
  'steer',
  'failed_turn_child_alive',
  'child_crash_mid_turn',
] as const;
export type ScenarioName = (typeof SCENARIO_NAMES)[number];

/** What one driver must report in one scenario. Rules 1 to 3 need no expectation. */
export interface ScenarioExpectation {
  /** One per accepted `write_prompt`, in order. */
  readonly turn_endings: readonly ConversationTurnEnding[];
  /** Every event class the driver reports, by class name, in any order. */
  readonly event_classes: readonly string[];
}
/** One driver's expectations, kept with its test. Any transcript of a scenario meets them. */
export type DriverExpectations = Readonly<Record<ScenarioName, ScenarioExpectation>>;
export interface ReplayScenario {
  readonly name: ScenarioName;
  readonly steps: readonly ReplayStep[];
}

const end_turn: ReplayStep = { step: 'await_event', event_class: 'TurnEnded' };
const stop: ReplayStep = { step: 'stop' };

export const TWO_TURNS: ReplayScenario = {
  name: 'two_turns',
  steps: [
    { step: 'write_prompt', text: '[CASE:plain] first replay turn' },
    end_turn,
    { step: 'write_prompt', text: '[CASE:plain] second replay turn' },
    end_turn,
    stop,
  ],
};
export const TOOL_CALL: ReplayScenario = {
  name: 'tool_call',
  steps: [
    { step: 'write_prompt', text: '[CASE:permission] use the tools' },
    { step: 'await_event', event_class: 'PermissionAskRaised' },
    { step: 'answer_permission_ask', option_kind: 'allow_once' },
    end_turn,
    stop,
  ],
};
export const INTERRUPT: ReplayScenario = {
  name: 'interrupt',
  steps: [
    { step: 'write_prompt', text: '[CASE:interrupt-base] start a long turn' },
    { step: 'cancel_running_turn' },
    end_turn,
    stop,
  ],
};
export const STEER: ReplayScenario = {
  name: 'steer',
  steps: [
    { step: 'write_prompt', text: '[CASE:steer-base] start a turn to steer' },
    { step: 'steer', text: '[CASE:steer] steer the running turn' },
    end_turn,
    stop,
  ],
};
export const FAILED_TURN_CHILD_ALIVE: ReplayScenario = {
  name: 'failed_turn_child_alive',
  steps: [
    { step: 'write_prompt', text: '[CASE:failed] this turn fails' },
    end_turn,
    { step: 'write_prompt', text: '[CASE:plain] the same child takes this one' },
    end_turn,
    stop,
  ],
};
export const CHILD_CRASH_MID_TURN: ReplayScenario = {
  name: 'child_crash_mid_turn',
  steps: [
    { step: 'write_prompt', text: '[CASE:crash7] the child dies in this turn' },
    end_turn,
    stop,
  ],
};
export const SCENARIOS: readonly ReplayScenario[] = [
  TWO_TURNS,
  TOOL_CALL,
  INTERRUPT,
  STEER,
  FAILED_TURN_CHILD_ALIVE,
  CHILD_CRASH_MID_TURN,
];
