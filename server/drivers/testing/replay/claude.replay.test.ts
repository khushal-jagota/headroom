/** Claude's real adapter and Agent SDK transport against replayed stream-json frames. */
import type { DriverExpectations } from './scenarios.ts';
import { replay_driver_tests } from './rules.ts';

/** Every run starts by reporting these, with a null token, before its first turn's events. */
const OPENING = ['VendorSessionCursorRebound', 'ComposerCatalogReported'];
const CLAUDE: DriverExpectations = {
  two_turns: {
    turn_endings: ['completed', 'completed'],
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
    event_classes: [...OPENING, 'TokenUsageReported', 'TurnEnded'],
  },
  steer: {
    turn_endings: ['completed'],
    event_classes: [...OPENING, 'AgentMessageCompleted', 'TokenUsageReported', 'TurnEnded'],
  },
  failed_turn_child_alive: {
    turn_endings: ['failed', 'completed'],
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
    event_classes: [...OPENING, 'TurnEnded', 'BackendStreamEnded'],
  },
};
replay_driver_tests('claude', CLAUDE);
