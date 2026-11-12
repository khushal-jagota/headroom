/** One pure cross-language codec exchange. No provider, server, or socket starts. */
import { readFileSync } from 'node:fs';
import * as backend from '../../conversation/backends/contracts.ts';
import { require_message_content } from '../../conversation/message_content.ts';
import {
  numeric_value,
  stringify_pydantic_json,
  stringify_python_json,
} from '../../core/http/numeric_json.ts';
import {
  as_object,
  decode_bounded,
  decode_event_arguments,
  decode_json,
  decode_value,
  encode_backend_event,
  encode_bounded,
  encode_frame,
  encode_value,
  read_typed_frame,
} from '../protocol.ts';
import { parse_executor_json } from '../protocol_json.ts';
import type { WireRootName } from '../wire.generated.ts';

type Fixture = { root: WireRootName; dto: unknown; python: string };
type Case = {
  name: string;
  root: WireRootName;
  dto: unknown;
  body: string;
  strict?: boolean;
  limit?: unknown;
  operation?: string;
};
const spec = parse_executor_json(Buffer.from(readFileSync(0))) as unknown as {
  fixtures: Fixture[];
  cases: Case[];
  events: Record<string, string>;
};
const bytes = (body: Uint8Array): string => Buffer.from(body).toString('base64');
const dto_value = (value: unknown): Parameters<typeof encode_bounded>[0] =>
  value as Parameters<typeof encode_bounded>[0];
const fixtures: Record<string, unknown> = {};
const values: Record<string, unknown> = {};
for (const fixture of spec.fixtures) {
  const decoded = decode_bounded(Buffer.from(fixture.python, 'base64'), fixture.root);
  values[fixture.root] = decoded;
  fixtures[fixture.root] = {
    read: bytes(encode_bounded(decoded, fixture.root)),
    // This input is the handwritten DTO, not the value decoded from Python.
    write: bytes(encode_bounded(dto_value(fixture.dto), fixture.root)),
    object: as_object(decoded, fixture.root),
    value: encode_value(decoded, fixture.root),
    value_bytes: stringify_pydantic_json(encode_value(decoded, fixture.root)),
  };
}
const cases = [];
for (const entry of spec.cases) {
  try {
    const body = Buffer.from(entry.body, 'base64');
    const limit = entry.limit === undefined ? undefined : numeric_value(entry.limit);
    let value: unknown;
    if (entry.operation === 'frame') {
      let offset = 0;
      value = await read_typed_frame(
        {
          async readexactly(length: number): Promise<Uint8Array> {
            const chunk = body.subarray(offset, offset + length);
            offset += length;
            if (chunk.byteLength !== length) throw new Error('fixture frame ended early');
            return chunk;
          },
        },
        entry.root,
      );
    } else if (entry.operation === 'value') value = decode_value(entry.dto, entry.root);
    else if (entry.operation === 'encode') value = dto_value(entry.dto);
    else if (entry.operation === 'json')
      value = decode_json(body.toString('utf8'), entry.root, { strict: entry.strict });
    else value = decode_bounded(body, entry.root, { strict: entry.strict, limit });
    cases.push({
      name: entry.name,
      ok: true,
      body: bytes(encode_bounded(value, entry.root, entry.operation === 'encode' ? { limit } : {})),
    });
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    cases.push({
      name: entry.name,
      ok: false,
      type: error.constructor.name,
      message: error.message,
    });
  }
}
const events: Record<string, unknown> = {};
const constructors = backend as unknown as Record<string, new (...args: never[]) => unknown>;
for (const [root, method] of Object.entries(spec.events)) {
  const event = decode_event_arguments({
    child: values.ChildReference,
    turn: values.TurnToken,
    method,
    arguments: values[root],
  } as Parameters<typeof decode_event_arguments>[0]);
  const constructor = constructors[root];
  const [encoded_method, arguments_value] = encode_backend_event(event);
  events[root] = {
    class: event.constructor.name,
    instance: constructor !== undefined && event instanceof constructor,
    method: encoded_method,
    arguments: arguments_value,
    pieces:
      event instanceof backend.AgentMessageCompleted
        ? require_message_content(event.content).map((piece) => piece.constructor.name)
        : null,
  };
}
const response = spec.fixtures.find((fixture) => fixture.root === 'IpcResponse')!;
const result = {
  fixtures,
  cases,
  events,
  frame: bytes(encode_frame(dto_value(response.dto), 'IpcResponse')),
};
process.stdout.write(stringify_python_json(result, { compact: true }));
