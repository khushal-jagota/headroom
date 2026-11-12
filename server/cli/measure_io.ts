/** Shared output contract for bounded measure-family commands. */
import type { CommandResult } from './contracts.ts';
import { emit as emit_text, json } from './http.ts';
export function emit(data: any, as_json: boolean): CommandResult {
  let human = json(data, { ensure_ascii: false, indent: 2 });
  if (
    data !== null &&
    typeof data === 'object' &&
    data.page !== null &&
    typeof data.page === 'object'
  ) {
    const page = data.page;
    human += `\nReturned ${json(page.return_count)} of ${json(page.match_count)} matches.`;
    human += `\nOmitted ${json(page.omitted_before)} before and ${json(page.omitted_after)} after.`;
    if (page.next_offset !== null)
      human += `\nUse --limit ${json(page.limit)} --offset ${json(page.next_offset)} for the next page.`;
  }
  return emit_text(data, as_json, human);
}
