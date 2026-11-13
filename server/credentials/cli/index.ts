/** Names-only credential commands never accept or print secret values. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { send, emit as emit_text, fail_validation } from '../../cli/http.ts';
import { emit } from '../../cli/measure_io.ts';
import { python_text } from '../../cli/python_text.ts';
import { response_field } from '../../cli/response_field.ts';
const quote = (value: string) =>
  encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
export async function run_credential_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const command = inv.command.path[1];
  if (command === 'list')
    return emit(
      await send(inv, 'GET', '/api/credentials', { params: { limit: v.limit, offset: v.offset } }),
      inv,
    );
  if (command === 'rename')
    return emit(
      await send(inv, 'PATCH', '/api/credentials/' + quote(v.credential_id), {
        json_body: { name: v.name, expected_revision: v.expected_revision },
      }),
      inv,
    );
  if ((v.replace_id === null) !== (v.expected_revision === null))
    fail_validation(inv, 'replacement requires --replace-id and --expected-revision');
  const body = {
    request_id: v.request_id,
    name: v.name,
    provider: v.provider,
    use: v.use_context,
    auth_kind: v.auth_kind,
    header_name: v.header_name,
    help: v.help_hint,
    help_url: v.help_url,
    replacement:
      v.replace_id === null
        ? null
        : { credential_id: v.replace_id, name: v.name, revision: v.expected_revision },
  };
  const result = await send(inv, 'POST', '/api/credentials/requests', { json_body: body });
  return v.as_json
    ? emit(result, inv)
    : emit_text(
        result,
        false,
        `API key request ${python_text(response_field(inv, result, 'state'))}: ${python_text(response_field(inv, result, 'name'))}`,
      );
}
