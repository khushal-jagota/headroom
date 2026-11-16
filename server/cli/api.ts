/** Authenticated requests for API operations without a dedicated CLI command. */
import type { CommandHandler } from './contracts.ts';
import { emit, fail_validation, json, send } from './http.ts';
import { read_source } from './input.ts';
import { parse_numeric_json } from '../core/http/numeric_json.ts';

export const run_api_command: CommandHandler = async (invocation) => {
  let body: unknown = undefined;
  if (invocation.values.input_json != null) {
    const source = await read_source(invocation, invocation.values.input_json);
    try {
      body = parse_numeric_json(source);
    } catch (error) {
      fail_validation(
        invocation,
        `invalid request JSON: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  const answer = await send(
    invocation,
    invocation.values.method.toUpperCase(),
    invocation.values.path,
    { json_body: body === null ? undefined : body },
  );
  return emit(answer, invocation.values.as_json, json(answer, { indent: 2 }));
};
