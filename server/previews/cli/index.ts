/** Application preview commands only use the caller's canonical HTTP identity. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send, emit } from '../../cli/http.ts';
import { scalar_text } from '../../cli/text.ts';
function human(data: Record<string, any>): string {
  if (data.preview === null) return 'No application preview is prepared.';
  return Object.entries(data)
    .filter(([, value]) => value !== null)
    .map(([key, value]) => `${key}: ${scalar_text(value)}`)
    .join('\n');
}
export async function run_preview_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const command = inv.command.path[1];
  const data =
    command === 'prepare'
      ? await send(inv, 'POST', '/api/previews/prepare')
      : command === 'register'
        ? await send(inv, 'POST', '/api/previews/register', { json_body: { port: v.port } })
        : await send(inv, command === 'stop' ? 'DELETE' : 'GET', '/api/previews');
  return emit(data, v.as_json, command === 'stop' ? 'Application preview ended.' : human(data));
}
