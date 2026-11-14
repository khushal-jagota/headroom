/** Native JSON values admitted by the shared executor schemas. */
import { Ajv2020 } from 'ajv/dist/2020.js';
import type { ValidateFunction } from 'ajv';
import { isDeepStrictEqual } from 'node:util';
import { WIRE_SCHEMA } from './wire.generated.ts';
import type {
  ChildCommand,
  JsonValue,
  MailboxCommand,
  WireRootName,
  WireTypes,
} from './wire.generated.ts';
import { ExecutorValidationError } from './protocol_errors.ts';

type ReadonlyWire<T> = T extends readonly (infer Item)[]
  ? readonly ReadonlyWire<Item>[]
  : T extends object
    ? { readonly [Key in keyof T]: ReadonlyWire<T[Key]> }
    : T;
export type WireInput<K extends WireRootName> = ReadonlyWire<WireTypes[K]>;

function command_admission(command: ChildCommand): boolean {
  const args = command.arguments;
  if (args.kind !== command.operation)
    throw new ExecutorValidationError('Command operation conflicts with its arguments.');
  if ((args.kind === 'write_prompt' || args.kind === 'steer') && command.turn === null)
    throw new ExecutorValidationError('Prompt commands require their exact turn.');
  if (args.kind === 'answer_permission_ask' || args.kind === 'answer_user_input') {
    const answer = args.answer;
    if (answer !== null && answer !== undefined) {
      const ask = answer.ask;
      const matching_command =
        answer.answer_id === command.command_id &&
        isDeepStrictEqual(ask.child, command.child) &&
        isDeepStrictEqual(ask.turn, command.turn);
      let matching_answer = false;
      if (args.kind === 'answer_permission_ask')
        matching_answer =
          ask.kind === 'permission' &&
          ask.ask_id === args.ask_id &&
          answer.option_id === args.option_id &&
          answer.user_input_answers == null;
      else if (args.kind === 'answer_user_input')
        matching_answer =
          ask.kind === 'user_input' &&
          ask.ask_id === args.request_id &&
          isDeepStrictEqual(answer.user_input_answers, args.answers) &&
          answer.option_id === null;
      if (!matching_command || !matching_answer)
        throw new ExecutorValidationError('Durable answer conflicts with its command.');
    }
  }
  return true;
}
function mailbox_admission(command: MailboxCommand): boolean {
  if ((command.child_command === null) === (command.model_list_request === null))
    throw new ExecutorValidationError('A mailbox command requires exactly one payload.');
  if (command.child_command && command.child_command.command_id !== command.command_id)
    throw new ExecutorValidationError('Mailbox command ID conflicts with its child command.');
  return true;
}
const ajv = new Ajv2020({
  strict: false,
  strictNumbers: true,
  validateFormats: false,
  coerceTypes: false,
  useDefaults: true,
  removeAdditional: false,
  discriminator: true,
});
ajv.addKeyword({
  keyword: 'executorCommand',
  schemaType: 'boolean',
  post: true,
  validate: (_schema: boolean, value: ChildCommand) => command_admission(value),
});
ajv.addKeyword({
  keyword: 'executorMailbox',
  schemaType: 'boolean',
  post: true,
  validate: (_schema: boolean, value: MailboxCommand) => mailbox_admission(value),
});
const schema_id = 'https://panels.invalid/executor-wire';
ajv.addSchema({ $id: schema_id, $defs: WIRE_SCHEMA.$defs });
const validators = new Map<string, ValidateFunction>();
function validator(root: keyof typeof WIRE_SCHEMA.$defs): ValidateFunction {
  let validate = validators.get(root);
  if (!validate) {
    if (!Object.hasOwn(WIRE_SCHEMA.$defs, root))
      throw new ExecutorValidationError(`Unknown executor type: ${root}`);
    validate = ajv.getSchema(`${schema_id}#/$defs/${root}`);
    if (!validate) throw new Error(`Missing executor schema: ${root}`);
    validators.set(root, validate);
  }
  return validate;
}
/** Probe a union branch without applying its defaults to the original message. */
export function matches_schema(value: unknown, root: keyof typeof WIRE_SCHEMA.$defs): boolean {
  return validator(root)(structuredClone(value));
}
export function decode_value<K extends WireRootName>(value: unknown, root: K): WireTypes[K] {
  const validate = validator(root);
  if (!validate(value))
    throw new ExecutorValidationError(
      `Invalid ${root}: ${ajv.errorsText(validate.errors)}`,
      validate.errors ?? [],
    );
  return value as WireTypes[K];
}
/** Native parser and serializer callbacks share the finite-number rule. */
export function finite_json_value(_key: string, value: unknown): unknown {
  if (typeof value === 'number' && !Number.isFinite(value))
    throw new ExecutorValidationError('Executor JSON numbers must be finite.');
  return value;
}
/** The native JSON serializer is the one conversion from native classes to wire data. */
export function encode_value<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): JsonValue {
  let plain: unknown;
  try {
    const text = JSON.stringify(value, finite_json_value);
    plain = JSON.parse(text);
  } catch (error) {
    if (error instanceof ExecutorValidationError) throw error;
    throw new ExecutorValidationError(
      `Cannot encode executor JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  return decode_value(plain, root) as JsonValue;
}
