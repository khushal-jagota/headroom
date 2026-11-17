/** Hermes' real adapter and ACP client against replayed JSON-RPC 2.0 frames. */
import type { DriverExpectations } from './scenarios.ts';
import { replay_driver_tests } from './rules.ts';

/** Every Hermes run starts a new session, then hears the session's commands. */
const STARTED = ['VendorSessionCursorRebound', 'ComposerCatalogReported'];
const ANSWERED = ['AgentMessageDelta', 'AgentMessageCompleted', 'TurnEnded'];

const HERMES: DriverExpectations = {
  two_turns: {
    turn_endings: ['completed', 'completed'],
    event_classes: [...STARTED, ...ANSWERED, 'ModelThinkingHappened'],
  },
  tool_call: {
    turn_endings: ['completed'],
    event_classes: [
      ...STARTED,
      ...ANSWERED,
      'ToolCallStarted',
      'PermissionAskRaised',
      'ToolCallProgress',
      'ToolCallFinished',
    ],
  },
  interrupt: { turn_endings: ['interrupted'], event_classes: [...STARTED, 'TurnEnded'] },
  steer: { turn_endings: ['completed'], event_classes: [...STARTED, ...ANSWERED] },
  failed_turn_child_alive: {
    turn_endings: ['failed', 'completed'],
    event_classes: [...STARTED, ...ANSWERED],
  },
  // Hermes reports no BackendStreamEnded when its process dies mid-turn.
  child_crash_mid_turn: {
    turn_endings: ['failed'],
    event_classes: [...STARTED, ...ANSWERED, 'ModelThinkingHappened'],
  },
};
replay_driver_tests('hermes', HERMES);
