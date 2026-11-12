import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { SummaryRequest } from './contracts.ts';
import { load_evidence } from './data.ts';
import { build_summary, periods } from './logic/summary.ts';
export function read_summary(sql: ReadSql, request: SummaryRequest, now: number) {
  let current, previous;
  try {
    [current, previous] = periods(request, now);
  } catch (error) {
    throw new PlannerError('validation', error instanceof Error ? error.message : String(error));
  }
  const evidence = load_evidence(sql, previous.start_at, current.end_at);
  const result = build_summary(request, evidence, now);
  if (request.scope.kind !== 'goal') return result;
  const goal = evidence.destinations.find(
    (item) => item.kind === 'goal' && item.id === request.scope.id,
  );
  const shared_project_context = goal?.project_id
    ? {
        project_id: goal.project_id,
        totals: build_summary(
          { ...request, scope: { kind: 'project', id: goal.project_id } },
          evidence,
          now,
        ).totals,
        limitation:
          'Shared Project context overlaps across goals and is not direct goal consumption.',
      }
    : null;
  return { ...result, shared_project_context };
}
