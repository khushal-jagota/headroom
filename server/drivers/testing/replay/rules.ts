/**
 * The event rules of `conversation/backends/contracts.ts`, checked against one replay run.
 * Each check throws with the run's events when the rule does not hold.
 */
import { basename } from 'node:path';
import { expect, test } from 'vitest';
import {
  TOOL_KINDS,
  ToolCallStarted,
  TurnEnded,
} from '../../../conversation/backends/contracts.ts';
import type { ChildDisposition, TurnToken } from '../../../conversation/backends/contracts.ts';
import type { ReplayRun } from './harness.ts';
import { event_class_names, run_replay } from './harness.ts';
import { SCENARIOS } from './scenarios.ts';
import type { DriverExpectations, ScenarioExpectation } from './scenarios.ts';
import { transcript_paths } from './transcript.ts';

/** Rule 1: only these classes may carry a null turn token. */
export const NULL_TOKEN_CLASSES: ReadonlySet<string> = new Set([
  'VendorSessionCursorRebound',
  'ComposerCatalogReported',
  'BackgroundTaskStarted',
  'BackgroundTaskEnded',
  'BackendStreamEnded',
]);
/** Rule 3: the live deltas, never stored. */
export const LIVE_DELTA_CLASSES: readonly string[] = [
  'AgentMessageDelta',
  'ModelThinkingHappened',
  'ToolCallProgress',
];

function same(left: TurnToken, right: TurnToken): boolean {
  return left.conversation_id === right.conversation_id && left.turn_number === right.turn_number;
}
function described(run: ReplayRun): string {
  return `${run.transcript.scenario}/${run.transcript.driver_kind} events ${JSON.stringify(
    run.events.map(({ turn, event }) => `${event.constructor.name}@${turn?.turn_number ?? null}`),
  )}`;
}

/** Rule 1: every event outside the null-token classes carries an accepted turn's token. */
export function check_rule_1(run: ReplayRun): void {
  const broken = run.events.filter(
    ({ turn, event }) =>
      !NULL_TOKEN_CLASSES.has(event.constructor.name) &&
      (turn === null || !run.accepted_turns.some((accepted) => same(accepted, turn))),
  );
  if (broken.length)
    throw new Error(
      `rule 1: ${broken.map(({ event }) => event.constructor.name).join(', ')} without an accepted turn; ${described(run)}`,
    );
}
/** Rule 2: exactly one TurnEnded per accepted token, and none for a refused one. */
export function check_rule_2(run: ReplayRun): void {
  const endings = run.events.filter(({ event }) => event instanceof TurnEnded);
  for (const accepted of run.accepted_turns) {
    const count = endings.filter(({ turn }) => turn !== null && same(turn, accepted)).length;
    if (count !== 1)
      throw new Error(
        `rule 2: turn ${accepted.turn_number} ended ${count} times; ${described(run)}`,
      );
  }
  for (const refused of run.refused_turns)
    if (endings.some(({ turn }) => turn !== null && same(turn, refused)))
      throw new Error(`rule 2: refused turn ${refused.turn_number} ended; ${described(run)}`);
}
/** Each accepted turn's TurnEnded, in accepted order. */
function turn_endings(run: ReplayRun): (TurnEnded | undefined)[] {
  return run.accepted_turns.map(
    (accepted) =>
      run.events.find(
        ({ turn, event }) => event instanceof TurnEnded && turn !== null && same(turn, accepted),
      )?.event as TurnEnded | undefined,
  );
}
/** Rule 4: each accepted turn's TurnEnded says whether its child can take another prompt. */
export function check_rule_4(run: ReplayRun, expectation: ScenarioExpectation): void {
  expect(
    turn_endings(run).map((ended) => ended?.child_disposition),
    `rule 4: ${described(run)}`,
  ).toEqual(
    expectation.child_dispositions ?? run.accepted_turns.map((): ChildDisposition => 'reusable'),
  );
}
/** Rule 6: every tool call names one of TOOL_KINDS, in the expected order. */
export function check_rule_6(run: ReplayRun, expectation: ScenarioExpectation): void {
  const kinds = run.events
    .filter(({ event }) => event instanceof ToolCallStarted)
    .map(({ event }) => (event as ToolCallStarted).tool_kind);
  const known: readonly string[] = TOOL_KINDS;
  expect(
    kinds.filter((kind) => !known.includes(kind)),
    `rule 6: ${described(run)}`,
  ).toEqual([]);
  expect(kinds, `rule 6: ${described(run)}`).toEqual(expectation.tool_kinds ?? []);
}
/** The endings, in accepted order, and the classes the driver reported. */
export function check_expectation(run: ReplayRun, expectation: ScenarioExpectation): void {
  expect(
    turn_endings(run).map((ended) => ended?.ending),
    described(run),
  ).toEqual(expectation.turn_endings);
  expect([...new Set(event_class_names(run.events))].sort(), described(run)).toEqual(
    [...expectation.event_classes].sort(),
  );
}

/** Longer than the harness's own waits, so a stuck replay fails with the harness's message. */
export const REPLAY_TEST_TIMEOUT_MS = 60_000;

/** One test per transcript file of this driver, in every scenario, checking rules 1, 2, 4 and 6. */
export function replay_driver_tests(driver_kind: string, expectations: DriverExpectations): void {
  for (const scenario of SCENARIOS) {
    const paths = transcript_paths(scenario.name, driver_kind);
    test(`${scenario.name} has a ${driver_kind} transcript`, () => {
      expect(paths.length).toBeGreaterThan(0);
    });
    for (const path of paths)
      test(
        `${scenario.name} ${basename(path)} obeys rules 1, 2, 4 and 6`,
        async () => {
          const run = await run_replay(path, scenario);
          check_rule_1(run);
          check_rule_2(run);
          check_rule_4(run, expectations[scenario.name]);
          check_rule_6(run, expectations[scenario.name]);
          check_expectation(run, expectations[scenario.name]);
        },
        REPLAY_TEST_TIMEOUT_MS,
      );
  }
}
