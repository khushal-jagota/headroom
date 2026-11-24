/** During an install the CLI can reach the release before it. A missing new field must not print as an answer. */
import type { Invocation } from './contracts.ts';
import { CliFailure } from './errors.ts';
import { json } from './http.ts';

const MESSAGE =
  'The Panels server is older than this command. It is being updated. Run the command again in a few minutes.';
export function require_current_server(inv: Invocation, present: boolean): void {
  if (present) return;
  throw new CliFailure({
    exit_code: 1,
    stdout: '',
    stderr: inv.values.as_json
      ? json({ error: { code: 'server_too_old', message: MESSAGE, detail: {} } }) + '\n'
      : `error: ${MESSAGE}\n`,
  });
}
