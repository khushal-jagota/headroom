/** Attention reads and retained-collection setup remain ordinary HTTP clients. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send, fail_validation } from '../../cli/http.ts';
import { emit } from '../../cli/measure_io.ts';
import { setup_collection } from './setup.ts';
export async function run_attention_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  if (inv.command.path[1] === 'setup-collection') return emit(await setup_collection(inv), inv);
  const selections: [string, string][] = [];
  for (const kind of ['project', 'manager', 'ticket', 'goal'])
    if (v[kind + '_id'] !== null) selections.push([kind, v[kind + '_id']]);
  if (v.chief) selections.push(['chief', 'chief']);
  if (selections.length > 1) fail_validation(inv, 'Select only one attention scope.');
  const params: Record<string, any> = {
    group_by: v.group_by,
    sort: v.sort,
    limit: v.limit,
    offset: v.offset,
  };
  if (v.first_date !== null) params.first_date = v.first_date;
  if (v.last_date !== null) params.last_date = v.last_date;
  if (selections.length) {
    params.scope_kind = selections[0][0];
    if (params.scope_kind !== 'chief') params.scope_id = selections[0][1];
  }
  return emit(await send(inv, 'GET', '/api/attention/summary', { params }), inv);
}
