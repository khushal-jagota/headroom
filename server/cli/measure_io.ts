/** Shared output contract for bounded measure-family commands. */
import type { CommandResult, Invocation } from './contracts.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import { emit as emit_text, json } from './http.ts';
import { scalar_text } from './text.ts';
import { response_field } from './response_field.ts';
export function emit(data: any, inv: Invocation): CommandResult {
  let human = json(data, { ensure_ascii: false, indent: 2 });
  const page = data !== null && typeof data === 'object' ? data.page : undefined;
  if (
    page !== null &&
    typeof page === 'object' &&
    !Array.isArray(page) &&
    !(page instanceof JsonNumber)
  ) {
    human += `\nReturned ${scalar_text(response_field(inv, page, 'return_count', 'page.return_count'))} of ${scalar_text(response_field(inv, page, 'match_count', 'page.match_count'))} matches.`;
    human += `\nOmitted ${scalar_text(response_field(inv, page, 'omitted_before', 'page.omitted_before'))} before and ${scalar_text(response_field(inv, page, 'omitted_after', 'page.omitted_after'))} after.`;
    if (page.next_offset !== null && page.next_offset !== undefined)
      human += `\nUse --limit ${scalar_text(response_field(inv, page, 'limit', 'page.limit'))} --offset ${scalar_text(page.next_offset)} for the next page.`;
  }
  return emit_text(data, inv.values.as_json, human);
}
