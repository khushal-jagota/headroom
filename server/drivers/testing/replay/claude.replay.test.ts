/** Claude's real adapter and Agent SDK transport against replayed stream-json frames. */
import type { DriverExpectations } from './scenarios.ts';
import { replay_driver_tests } from './rules.ts';

/** Every run starts by reporting these, with a null token, before its first turn's events. */
const OPENING = ['VendorSessionCursorRebound', 'ComposerCatalogReported'];
const CLAUDE: DriverExpectations = {
  two_turns: {
    turn_endings: ['completed', 'completed'],
    child_dispositions: ['reusable', 'reusable'],
    event_classes: [
      ...OPENING,
      'ModelThinkingHappened',
      'AgentMessageDelta',
      'AgentMessageCompleted',
      'TokenUsageReported',
      'TurnEnded',
    ],
  },
  tool_call: {
    turn_endings: ['completed'],
    child_dispositions: ['reusable'],
    tool_kinds: ['edit', 'read', 'execute', 'edit', 'search', 'fetch', 'think'],
    event_classes: [
      ...OPENING,
      'ToolCallStarted',
      'PermissionAskRaised',
      'ToolCallProgress',
      'ToolCallFinished',
      'AgentMessageCompleted',
      'TokenUsageReported',
      'TurnEnded',
    ],
  },
  interrupt: {
    turn_endings: ['interrupted'],
    child_dispositions: ['reusable'],
    event_classes: [...OPENING, 'TokenUsageReported', 'TurnEnded'],
  },
  steer: {
    turn_endings: ['completed'],
    child_dispositions: ['reusable'],
    event_classes: [
      ...OPENING,
      'ModelThinkingHappened',
      'AgentMessageDelta',
      'AgentMessageCompleted',
      'TokenUsageReported',
      'TurnEnded',
    ],
  },
  failed_turn_child_alive: {
    turn_endings: ['failed', 'completed'],
    child_dispositions: ['reusable', 'reusable'],
    event_classes: [
      ...OPENING,
      'AgentMessageDelta',
      'AgentMessageCompleted',
      'TokenUsageReported',
      'TurnEnded',
    ],
  },
  // The stream ends after the failed turn: only Claude reports BackendStreamEnded here.
  child_crash_mid_turn: {
    turn_endings: ['failed'],
    child_dispositions: ['broken'],
    event_classes: [...OPENING, 'TurnEnded', 'BackendStreamEnded'],
  },
};
replay_driver_tests('claude', CLAUDE);
