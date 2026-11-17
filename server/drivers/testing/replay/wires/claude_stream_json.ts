/** Claude Code `--input-format stream-json` lines, as the Agent SDK writes and reads them. */
import { is_record } from '../../../../core/records.ts';
import type { ReplayWireRules } from '../transcript.ts';

function option(argv: readonly string[], name: string): string | undefined {
  const inline = argv.find((argument) => argument.startsWith(`${name}=`));
  if (inline !== undefined) return inline.slice(name.length + 1);
  const position = argv.indexOf(name);
  return position < 0 ? undefined : argv[position + 1];
}
/** An SDK control request: `{type: 'control_request', request_id, request: {subtype}}`. */
function control_request(frame: unknown): { request_id: string; subtype: string } | null {
  if (!is_record(frame) || frame.type !== 'control_request' || !is_record(frame.request))
    return null;
  const { request_id } = frame;
  const { subtype } = frame.request;
  return typeof request_id === 'string' && typeof subtype === 'string'
    ? { request_id, subtype }
    : null;
}
export const CLAUDE_STREAM_JSON_WIRE: ReplayWireRules = {
  // The adapter mints the session and passes it on the command line.
  launch_placeholders: (argv): Record<string, string> => {
    const session = option(argv, '--session-id') ?? option(argv, '--resume');
    return session === undefined ? {} : { '<session>': session };
  },
  request_method: (frame) => control_request(frame)?.subtype ?? null,
  background_reply: (request, reply) => {
    const template = reply as { response: Record<string, unknown> };
    return {
      ...template,
      response: { ...template.response, request_id: control_request(request)!.request_id },
    };
  },
  // The SDK names its control requests and stamps each user message with a uuid.
  generated_values: (frame) => {
    const values: string[] = [];
    const request = control_request(frame);
    if (request !== null) values.push(request.request_id);
    if (is_record(frame) && frame.type === 'user' && typeof frame.uuid === 'string')
      values.push(frame.uuid);
    return values;
  },
};
