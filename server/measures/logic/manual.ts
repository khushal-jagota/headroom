import { createHash } from 'node:crypto';
import {
  numeric_kind,
  numeric_value,
  stringify_python_json,
} from '../../core/http/numeric_json.ts';
import type { ManualWriteBody, Observation } from '../contracts.ts';
import { calendar_date, invalid } from './dates.ts';
import { float_number, number } from './formulas.ts';
export function parse_write(raw: unknown): ManualWriteBody {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw))
    throw invalid('invalid manual measure write');
  const v = raw as Record<string, unknown>,
    op = v.operation,
    fields =
      op === 'replace'
        ? ['value', 'expected_write_revision']
        : op === 'increment'
          ? ['delta']
          : null;
  if (
    fields === null ||
    Object.keys(v).sort().join('|') !==
      ['request_id', 'date', 'expected_definition_revision', 'basis', 'operation', ...fields]
        .sort()
        .join('|')
  )
    throw invalid('invalid manual measure write');
  if (typeof v.request_id !== 'string' || !v.request_id.trim())
    throw invalid('manual write requires a request ID');
  if (typeof v.date !== 'string') throw invalid('invalid measure date');
  calendar_date(v.date);
  if (v.basis !== 'date' && v.basis !== 'latest') throw invalid('invalid manual write basis');
  if (
    numeric_kind(v.expected_definition_revision) !== 'integer' ||
    numeric_value(v.expected_definition_revision)! < 1
  )
    throw invalid('measure definition revision is required');
  const normalized = {
    ...v,
    expected_definition_revision: v.expected_definition_revision,
  };
  if (op === 'replace') {
    if (
      numeric_kind(v.expected_write_revision) !== 'integer' ||
      numeric_value(v.expected_write_revision)! < 0
    )
      throw invalid('manual write revision is required');
    return {
      ...normalized,
      expected_write_revision: v.expected_write_revision,
      value: float_number(v.value),
    } as unknown as ManualWriteBody;
  }
  return { ...normalized, delta: float_number(v.delta) } as unknown as ManualWriteBody;
}
export function fingerprint_bytes(measure_id: string, body: ManualWriteBody): string {
  return stringify_python_json(
    { measure_id, ...body },
    { sort_keys: true, compact: true, ensure_ascii: true },
  );
}
export function fingerprint(measure_id: string, body: ManualWriteBody): string {
  return createHash('sha256').update(fingerprint_bytes(measure_id, body)).digest('hex');
}
export function accepted_value(body: ManualWriteBody, base: Observation | null): number {
  return body.operation === 'replace'
    ? number(body.value)
    : number((base?.value ?? 0) + number(body.delta));
}
