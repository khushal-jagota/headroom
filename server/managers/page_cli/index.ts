/** Manager and Chief document commands use the existing holder-page routes. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { emit, fail_validation, json, send } from '../../cli/http.ts';
import { stdin_object } from '../../cli/input.ts';

const response = (data: unknown, as_json: boolean): CommandResult =>
  emit(data, as_json, json(data, { ensure_ascii: false, indent: 2 }));
function route(invocation: Invocation): string {
  const values = invocation.values;
  if (!!values.chief === (values.manager_id != null)) {
    fail_validation(invocation, 'Use exactly one of --manager ID or --chief.', {
      recovery: 'panels page --help',
    });
  }
  const manager = encodeURIComponent(values.manager_id ?? '').replace(
    /[!'()*]/g,
    (character) => '%' + character.charCodeAt(0).toString(16).toUpperCase(),
  );
  return values.chief ? '/api/chief/page' : `/api/managers/${manager}/page`;
}

export async function run_page_command(invocation: Invocation): Promise<CommandResult> {
  const as_json = invocation.values.as_json;
  const path = invocation.command.path.join(' ');
  switch (path) {
    case 'page schema': {
      const { page_guidance } = await import('./guidance.ts');
      return response(page_guidance, as_json);
    }
    case 'page show':
      return response(await send(invocation, 'GET', route(invocation)), as_json);
    case 'page edit': {
      const target = route(invocation);
      const body = await stdin_object(invocation);
      if (Object.keys(body).length !== 1 || !Object.hasOwn(body, 'page')) {
        fail_validation(
          invocation,
          'Provide exactly the page field. Use null to clear the holder document.',
          {
            recovery: 'panels page schema',
          },
        );
      }
      return response(await send(invocation, 'PATCH', target, { json_body: body }), as_json);
    }
    default:
      throw new Error(`No page command handler for ${path}`);
  }
}
