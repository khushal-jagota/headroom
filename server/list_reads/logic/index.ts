import { PlannerError } from '../../core/contracts.ts';
import type { ListPageFacts, ListPageRequest, ReadDetail } from '../contracts.ts';

export function parseReadDetail(raw: string | undefined): ReadDetail {
  const allowed = ['summary', 'full'];
  if (raw === undefined) throw new PlannerError('validation', 'detail is required', { allowed });
  if (raw !== 'summary' && raw !== 'full')
    throw new PlannerError('validation', 'unknown detail', { detail: raw, allowed });
  return raw;
}
export function rejectParameters(because: string, given: Record<string, unknown>): void {
  const parameters = Object.keys(given)
    .filter((key) => given[key] != null)
    .sort();
  if (parameters.length)
    throw new PlannerError('validation', 'parameter does not apply to this read', {
      parameters,
      because,
    });
}
export function requireFullForOne(detail: ReadDetail): void {
  if (detail !== 'full')
    throw new PlannerError('validation', 'reading one object requires detail=full', { detail });
}
export function checkPage(page: ListPageRequest): ListPageRequest {
  if (page.limit < 1)
    throw new PlannerError('validation', 'limit must be at least 1', { limit: page.limit });
  if (page.offset < 0)
    throw new PlannerError('validation', 'offset must be at least 0', { offset: page.offset });
  return page;
}
export function pageFacts(
  match_count: number,
  return_count: number,
  page: ListPageRequest,
): ListPageFacts {
  const omitted_before = Math.min(page.offset, match_count);
  const omitted_after = Math.max(match_count - omitted_before - return_count, 0);
  return {
    match_count,
    return_count,
    ...page,
    omitted_before,
    omitted_after,
    complete: omitted_before === 0 && omitted_after === 0,
    next_offset: omitted_after === 0 ? null : omitted_before + return_count,
  };
}
