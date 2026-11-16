/** Thin source commands: offline declarations, bounded input, and canonical HTTP writes. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send } from '../../cli/http.ts';
import { stdin_object } from '../../cli/input.ts';
import { emit } from '../../cli/measure_io.ts';
import { CREATE, RUN, SCRIPT, runtime_guide } from '../guidance.ts';
const quote = (value: string) =>
  encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
export async function run_measure_source_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const command = inv.command.path[1];
  if (command === 'schema')
    return emit(
      ({ create: CREATE, script: SCRIPT, run: RUN } as Record<string, unknown>)[v.operation],
      inv,
    );
  if (command === 'runtime') return emit(runtime_guide(), inv);
  const path = '/api/measure-sources';
  let data: unknown;
  if (command === 'list') {
    const params: Record<string, any> = { limit: v.limit, offset: v.offset };
    if (v.project_id !== null) params.project_id = v.project_id;
    data = await send(inv, 'GET', path, { params });
  } else if (command === 'create')
    data = await send(inv, 'POST', path, { json_body: await stdin_object(inv) });
  else if (command === 'run-show')
    data = await send(inv, 'GET', '/api/measure-source-runs/' + quote(v.run_id));
  else {
    const source = path + '/' + quote(v.source_id);
    if (command === 'show' || command === 'delete')
      data = await send(inv, command === 'show' ? 'GET' : 'DELETE', source);
    else if (command === 'status') data = await send(inv, 'GET', source + '/status');
    else if (command === 'runs')
      data = await send(inv, 'GET', source + '/runs', {
        params: { limit: v.limit, offset: v.offset },
      });
    else {
      const tail: Record<string, string> = {
        'set-schedule': 'schedule',
        'set-responsible': 'responsible',
        'submit-script': 'script',
        'request-run': 'runs',
      };
      data = await send(
        inv,
        command === 'request-run' ? 'POST' : 'PUT',
        source + '/' + tail[command],
        { json_body: await stdin_object(inv) },
      );
    }
  }
  return emit(data, inv);
}
