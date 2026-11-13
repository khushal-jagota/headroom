/** Dataset commands use the same bounded input and output contract as measures. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send } from '../../cli/http.ts';
import { stdin_object } from '../../cli/input.ts';
import { emit } from '../../cli/measure_io.ts';
const quote = (value: string) =>
  encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
export async function run_dataset_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const command = inv.command.path[1];
  const path = '/api/datasets';
  let data: unknown;
  if (command === 'list') {
    const params: Record<string, any> = { limit: v.limit, offset: v.offset };
    if (v.project_id !== null) params.project_id = v.project_id;
    if (v.source_id !== null) params.source_id = v.source_id;
    data = await send(inv, 'GET', path, { params });
  } else if (command === 'show') data = await send(inv, 'GET', path + '/' + quote(v.dataset_id));
  else {
    const endpoint =
      command === 'create'
        ? path
        : command === 'read'
          ? path + '/read'
          : path + '/' + quote(v.dataset_id);
    data = await send(inv, command === 'edit' ? 'PATCH' : 'POST', endpoint, {
      json_body: await stdin_object(inv),
    });
  }
  return emit(data, inv);
}
