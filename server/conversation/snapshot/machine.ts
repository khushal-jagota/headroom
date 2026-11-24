/** Admit a machine provider update result before the server returns it. */
import { HttpError } from '../../core/http/errors.ts';
import { is_record } from '../../core/records.ts';
import type { BackendUpdateResult } from './contracts.ts';
export function decode_machine_update(value: unknown): BackendUpdateResult {
  if (
    !is_record(value) ||
    !['succeeded', 'unchanged', 'failed'].includes(String(value.outcome)) ||
    typeof value.detail !== 'string' ||
    typeof value.output_tail !== 'string'
  )
    throw new HttpError(502, 'The machine returned an invalid provider update result.');
  return {
    outcome: value.outcome as BackendUpdateResult['outcome'],
    detail: value.detail,
    output_tail: value.output_tail,
  };
}
