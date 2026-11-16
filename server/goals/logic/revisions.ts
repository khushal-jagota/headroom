/** Revision metadata belongs to write admission, not the editable document. */
import { PlannerError } from '../../core/contracts.ts';
import { JsonNumber, numeric_kind } from '../../core/http/numeric_json.ts';

export function document_edit(
  body: Record<string, unknown>,
  current_revision: number,
): Record<string, unknown> {
  if (!Object.hasOwn(body, 'expected_revision')) return body;
  const revision = body.expected_revision;
  const integer =
    revision instanceof JsonNumber
      ? revision.integer
      : numeric_kind(revision) === 'integer'
        ? BigInt(revision as number)
        : null;
  if (integer === null || integer < 0n)
    throw new PlannerError('validation', 'expected_revision must be a nonnegative integer');
  if (integer !== BigInt(current_revision))
    throw new PlannerError(
      'revision_conflict',
      'this document changed; read it again before saving',
      {
        expected_revision: revision,
        current_revision,
      },
    );
  const changes = { ...body };
  delete changes.expected_revision;
  return changes;
}
