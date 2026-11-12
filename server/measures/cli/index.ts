/** Client-only measure commands. Domain validation remains at the HTTP boundary. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send } from '../../cli/http.ts';
import { stdin_object } from '../../cli/input.ts';
import { emit } from '../../cli/measure_io.ts';
import { CREATE } from './schema.ts';
const quote = (value: string) =>
  encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
export async function run_measure_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const command = inv.command.path[1];
  if (command === 'schema') return emit(CREATE, v.as_json);
  let data: unknown;
  const path = '/api/measures';
  if (command === 'list') {
    const params: Record<string, any> = { limit: v.limit, offset: v.offset };
    if (v.project_id !== null) params.project_id = v.project_id;
    if (v.source_id !== null) params.source_id = v.source_id;
    data = await send(inv, 'GET', path, { params });
  } else if (command === 'show' || command === 'delete') {
    data = await send(inv, command === 'show' ? 'GET' : 'DELETE', path + '/' + quote(v.measure_id));
  } else if (command === 'manual-state') {
    data = await send(inv, 'GET', path + '/' + quote(v.measure_id) + '/manual-state', {
      params: v.selected_date !== null ? { date: v.selected_date } : {},
    });
  } else {
    const endpoint =
      command === 'create'
        ? path
        : command === 'read'
          ? path + '/read'
          : path + '/' + quote(v.measure_id) + (command === 'manual-write' ? '/manual-writes' : '');
    data = await send(inv, command === 'edit' ? 'PATCH' : 'POST', endpoint, {
      json_body: await stdin_object(inv),
    });
  }
  return emit(data, v.as_json);
}
