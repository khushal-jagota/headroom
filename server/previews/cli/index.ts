/** Application preview commands only use the caller's canonical HTTP identity. */
import { python_string_repr } from '../../core/python_repr.ts';
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send, emit, json } from '../../cli/http.ts';
import { CliFailure, human_error } from '../../cli/errors.ts';
/** The preview process has only string and integer fields, in response order. */
function process_text(process: Record<string, unknown>): string {
  return (
    '{' +
    Object.entries(process)
      .map(
        ([key, value]) =>
          python_string_repr(key) +
          ': ' +
          (typeof value === 'string' ? python_string_repr(value) : json(value)),
      )
      .join(', ') +
    '}'
  );
}
function human(data: Record<string, any>): string {
  if (data.preview === null) return 'No application preview is prepared.';
  return Object.entries(data)
    .filter(([, value]) => value !== null)
    .map(
      ([key, value]) =>
        `${key}: ${typeof value === 'string' ? value : key === 'process' ? process_text(value) : json(value)}`,
    )
    .join('\n');
}
export async function run_preview_command(inv: Invocation): Promise<CommandResult> {
  if (inv.environment.PLAN_EXECUTOR_ROOT)
    throw new CliFailure(
      human_error(
        { ...inv.command, path: ['preview'], parameters: [] },
        'Application previews are unavailable for Tickets running on the Mac.',
      ),
    );
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
