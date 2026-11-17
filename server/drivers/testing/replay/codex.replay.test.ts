/** Codex's real adapter and app-server client against replayed JSON-RPC frames. */
import type { DriverExpectations } from './scenarios.ts';
import { replay_driver_tests } from './rules.ts';

/** Every Codex run starts a thread and publishes its composer catalogue, both with no turn. */
const STARTED = ['VendorSessionCursorRebound', 'ComposerCatalogReported'];
const ANSWERED = [...STARTED, 'AgentMessageDelta', 'AgentMessageCompleted', 'TokenUsageReported'];

const CODEX: DriverExpectations = {
  two_turns: {
    turn_endings: ['completed', 'completed'],
    event_classes: [...ANSWERED, 'TurnEnded'],
  },
  tool_call: {
    turn_endings: ['completed'],
    event_classes: [
      ...ANSWERED,
      'ModelThinkingHappened',
      'ToolCallStarted',
      'PermissionAskRaised',
      'ToolCallFinished',
      'TurnEnded',
    ],
  },
  // The interrupt lands before the model says anything.
  interrupt: { turn_endings: ['interrupted'], event_classes: [...STARTED, 'TurnEnded'] },
  steer: { turn_endings: ['completed'], event_classes: [...ANSWERED, 'TurnEnded'] },
  failed_turn_child_alive: {
    turn_endings: ['failed', 'completed'],
    event_classes: [...ANSWERED, 'TurnEnded'],
  },
  // Codex reports no BackendStreamEnded when its process dies: the turn's failed ending says it.
  child_crash_mid_turn: {
    turn_endings: ['failed'],
    event_classes: [...STARTED, 'AgentMessageDelta', 'AgentMessageCompleted', 'TurnEnded'],
  },
};
replay_driver_tests('codex', CODEX);
