/** The current executor schema grammar, source validation modes and value serializer. */
import { pure_posix_path } from '../core/python_path.ts';
import {
  is_json_record,
  JsonNumber,
  numeric_value,
  stringify_pydantic_json,
} from '../core/http/numeric_json.ts';
import { strip_unicode_space } from '../core/whitespace.ts';
import { WIRE_SCHEMA } from './wire.generated.ts';
import type { JsonValue, WireRootName, WireTypes } from './wire.generated.ts';
import { normalize_integer, normalize_json_number } from './integers.ts';
import { EXECUTOR_JSON_MAX_DEPTH, EXECUTOR_JSON_MAX_INTEGER_LENGTH } from './configuration.ts';
import { ordered_json_entries, ordered_json_object } from './protocol_json.ts';
import {
  class_name,
  tuple_value,
  tag_class,
  tag_tuple,
  ValidationError,
  KeyError,
  python_repr,
  ValueError,
} from './protocol_errors.ts';
import type { FieldRepresentation, Issue } from './protocol_errors.ts';
export interface Schema {
  $ref?: string;
  type?: string;
  enum?: readonly unknown[];
  const?: unknown;
  anyOf?: readonly Schema[];
  oneOf?: readonly Schema[];
  properties?: Record<string, Schema>;
  required?: readonly string[];
  additionalProperties?: boolean | Schema;
  default?: unknown;
  items?: Schema;
  prefixItems?: readonly Schema[];
  maxItems?: number;
  format?: string;
  'x-python-fields'?: readonly string[];
  'x-python-container'?: 'tuple' | 'list';
  'x-python-enum'?: { name: string; members: readonly (readonly [string, string])[] };
}
const document = WIRE_SCHEMA as unknown as {
  $defs: Record<string, Schema>;
  pydantic_error_version: string;
};
export const ERROR_VERSION = document.pydantic_error_version;
const definitions = document.$defs;
const json_value_items = definitions.JsonValue!.anyOf!.find(
  (schema) => schema.type === 'array',
)!.items!;
const BAD = Symbol('invalid executor value');
type ReadonlyWire<T> = T extends JsonNumber
  ? T
  : T extends readonly (infer Item)[]
    ? readonly ReadonlyWire<Item>[]
    : T extends object
      ? { readonly [Key in keyof T]: ReadonlyWire<T[Key]> }
      : T;
export type WireInput<K extends WireRootName> = ReadonlyWire<WireTypes[K]>;
type Context = {
  strict: boolean;
  origin: 'json' | 'python';
  pythonInput: boolean;
  issues: Issue[];
  revalidate: boolean;
};
const record = (v: unknown): v is Record<string, unknown> =>
  is_json_record(v) && !(v instanceof Uint8Array) && !class_name(v);
function dereference(schema: Schema): Schema {
  return schema.$ref ? definitions[schema.$ref.slice('#/$defs/'.length)]! : schema;
}
function expected(values: readonly unknown[]): string {
  return values
    .map(python_repr)
    .join(', ')
    .replace(/, ([^,]+)$/, ' or $1');
}
function fail(
  ctx: Context,
  type: string,
  message: string,
  input: unknown,
  loc: readonly (string | number)[],
  url = true,
): typeof BAD {
  ctx.issues.push({ type, message, input, loc: loc.slice(), url });
  return BAD;
}
function field_repr(name: string, schema: Schema): FieldRepresentation {
  schema = dereference(schema);
  if (schema.anyOf) schema = dereference(schema.anyOf.find((s) => s.type !== 'null')!);
  return {
    name,
    enumeration: schema['x-python-enum'],
    path: schema.format === 'path',
    floating: schema.type === 'number',
  };
}
type Branch = { schema: Schema; label: string; fields: readonly string[]; name?: string };
const fields_cache = new WeakMap<Schema, readonly FieldRepresentation[]>();
const branch_cache = new WeakMap<Schema, readonly Branch[]>();
const tags_cache = new WeakMap<Schema, readonly string[]>();
function representations(schema: Schema): readonly FieldRepresentation[] {
  let cached = fields_cache.get(schema);
  if (!cached) {
    cached = schema['x-python-fields']!.map((key) => field_repr(key, schema.properties![key]!));
    fields_cache.set(schema, cached);
  }
  return cached;
}
function branches(schema: Schema): readonly Branch[] {
  let cached = branch_cache.get(schema);
  if (!cached) {
    cached = schema.anyOf!.map((branch) => {
      const target = dereference(branch);
      const name = branch.$ref?.slice('#/$defs/'.length);
      return {
        schema: branch,
        name,
        label:
          name && target['x-python-fields']
            ? name
            : `literal[${(target.enum ?? [target.const]).map(python_repr).join(',')}]`,
        fields: Object.keys(target.properties ?? {}),
      };
    });
    branch_cache.set(schema, cached);
  }
  return cached;
}
function default_value(schema: Schema): unknown {
  const value = schema.default;
  const node = dereference(schema.anyOf?.find((s) => s.type !== 'null') ?? schema);
  if (Array.isArray(value)) {
    const result = value.slice();
    if (node['x-python-container'] === 'tuple') tag_tuple(result);
    return result;
  }
  if (typeof value === 'number') return node.type === 'integer' ? normalize_integer(value) : value;
  return value;
}
function scalar(
  schema: Schema,
  value: unknown,
  ctx: Context,
  loc: readonly (string | number)[],
): unknown {
  const numeric = numeric_value(value);
  if (schema.type === 'string') {
    if (typeof value === 'string') return schema.format === 'path' ? pure_posix_path(value) : value;
    if (!ctx.strict && ctx.pythonInput && value instanceof Uint8Array) {
      try {
        const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(value);
        return schema.format === 'path' ? pure_posix_path(text) : text;
      } catch {}
    }
    return schema.format === 'path'
      ? fail(
          ctx,
          'path_type',
          "Input is not a valid path for <class 'pathlib.Path'>",
          value,
          loc,
          false,
        )
      : fail(ctx, 'string_type', 'Input should be a valid string', value, loc);
  }
  if (schema.type === 'boolean') {
    if (typeof value === 'boolean') return value;
    if (!ctx.strict) {
      if (numeric === 0 || numeric === 1) return Boolean(numeric);
      if (typeof value === 'string') {
        if (['0', 'off', 'f', 'false', 'n', 'no'].includes(value.toLowerCase())) return false;
        if (['1', 'on', 't', 'true', 'y', 'yes'].includes(value.toLowerCase())) return true;
        return fail(
          ctx,
          'bool_parsing',
          'Input should be a valid boolean, unable to interpret input',
          value,
          loc,
        );
      }
      if (numeric !== undefined && Number.isFinite(numeric) && Number.isInteger(numeric))
        return fail(
          ctx,
          'bool_parsing',
          'Input should be a valid boolean, unable to interpret input',
          value,
          loc,
        );
    }
    return fail(ctx, 'bool_type', 'Input should be a valid boolean', value, loc);
  }
  if (schema.type === 'integer') {
    if (value instanceof JsonNumber && value.kind === 'integer') return normalize_integer(value);
    if (typeof value === 'number' && Number.isSafeInteger(value) && !Object.is(value, -0))
      return normalize_integer(value);
    if (!ctx.strict) {
      if (typeof value === 'boolean') return value ? 1 : 0;
      if (numeric !== undefined) {
        if (!Number.isFinite(numeric))
          return fail(ctx, 'finite_number', 'Input should be a finite number', value, loc);
        if (Number.isInteger(numeric)) {
          if (Math.abs(numeric) >= 2 ** 63)
            return fail(
              ctx,
              'int_parsing_size',
              'Unable to parse input string as an integer, exceeded maximum size',
              value,
              loc,
            );
          return normalize_integer(BigInt(numeric));
        }
        return fail(
          ctx,
          'int_from_float',
          'Input should be a valid integer, got a number with a fractional part',
          value,
          loc,
        );
      }
      if (typeof value === 'string') {
        const text = strip_unicode_space(value);
        if (/^[+-]?[0-9]+(?:_[0-9]+)*(?:\.0+)?$/.test(text)) {
          const integer = text.replaceAll('_', '').replace(/\.0+$/, '');
          if (integer.length <= 4300) return normalize_integer(BigInt(integer));
          return fail(
            ctx,
            'int_parsing_size',
            'Unable to parse input string as an integer, exceeded maximum size',
            value,
            loc,
          );
        }
        return fail(
          ctx,
          'int_parsing',
          'Input should be a valid integer, unable to parse string as an integer',
          value,
          loc,
        );
      }
    }
    return fail(ctx, 'int_type', 'Input should be a valid integer', value, loc);
  }
  if (schema.type === 'number') {
    if (numeric !== undefined) {
      return value instanceof JsonNumber && value.kind === 'integer' && Object.is(numeric, -0)
        ? 0
        : numeric;
    }
    if (!ctx.strict && typeof value === 'boolean') return value ? 1 : 0;
    if (!ctx.strict && typeof value === 'string') {
      const raw = strip_unicode_space(value);
      const text =
        raw.startsWith('_') || raw.endsWith('_') || raw.includes('__')
          ? ''
          : raw.replaceAll('_', '');
      if (/^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|inf(?:inity)?|nan)$/i.test(text)) {
        const n = /^[+-]?inf(?:inity)?$/i.test(text)
          ? text.startsWith('-')
            ? -Infinity
            : Infinity
          : Number(text);
        return n;
      }
      return fail(
        ctx,
        'float_parsing',
        'Input should be a valid number, unable to parse string as a number',
        value,
        loc,
      );
    }
    return fail(ctx, 'float_type', 'Input should be a valid number', value, loc);
  }
  return value === null ? null : fail(ctx, 'none_required', 'Input should be None', value, loc);
}
function node(
  schema: Schema,
  value: unknown,
  ctx: Context,
  loc: (string | number)[],
  name?: string,
  depth = 0,
): unknown {
  if (schema.$ref) {
    const key = schema.$ref.slice('#/$defs/'.length);
    return node(definitions[key]!, value, ctx, loc, key, depth);
  }
  if (name === 'JsonValue') {
    if (
      value === null ||
      typeof value === 'string' ||
      typeof value === 'boolean' ||
      typeof value === 'number' ||
      value instanceof JsonNumber
    )
      return value instanceof JsonNumber ? normalize_json_number(value) : value;
    if (Array.isArray(value)) {
      if (ctx.revalidate && value.length && depth >= EXECUTOR_JSON_MAX_DEPTH)
        throw new ValueError('recursion limit exceeded');
      const result: unknown[] = [];
      for (let i = 0; i < value.length; i++) {
        loc.push(i);
        result.push(node(json_value_items, value[i], ctx, loc, undefined, depth + 1));
        loc.pop();
      }
      return result;
    }
    if (record(value)) {
      if (ctx.revalidate && depth >= EXECUTOR_JSON_MAX_DEPTH && Object.keys(value).length)
        throw new ValueError('recursion limit exceeded');
      const entries: [string, unknown][] = [];
      for (const [key, item] of ordered_json_entries(value)) {
        loc.push(key);
        entries.push([key, node(schema, item, ctx, loc, 'JsonValue', depth + 1)]);
        loc.pop();
      }
      return ordered_json_object(entries);
    }
  }
  if (schema.oneOf) {
    if (!record(value) && !class_name(value))
      return fail(
        ctx,
        'model_attributes_type',
        ctx.origin === 'json'
          ? 'Input should be an object'
          : 'Input should be a valid dictionary or object to extract fields from',
        value,
        loc,
      );
    const tag =
      record(value) || class_name(value) ? (value as Record<string, unknown>).kind : undefined;
    let choices = tags_cache.get(schema);
    if (!choices) {
      choices = schema.oneOf.map((s) => dereference(s).properties!.kind!.const as string);
      tags_cache.set(schema, choices);
    }
    if (typeof tag !== 'string' || !choices.includes(tag))
      return fail(
        ctx,
        tag === undefined ? 'union_tag_not_found' : 'union_tag_invalid',
        tag === undefined
          ? "Unable to extract tag using discriminator 'kind'"
          : `Input tag '${typeof tag === 'string' ? tag : python_repr(tag)}' found using 'kind' does not match any of the expected tags: ${choices.map(python_repr).join(', ')}`,
        value,
        loc,
      );
    loc.push(tag);
    const result = node(schema.oneOf[choices.indexOf(tag)]!, value, ctx, loc, undefined, depth);
    loc.pop();
    return result;
  }
  if (schema.anyOf) {
    if (schema.anyOf.some((s) => s.type === 'null'))
      return value === null
        ? null
        : node(
            schema.anyOf.find((s) => s.type !== 'null')!,
            value,
            ctx,
            loc,
            undefined,
            depth,
          );
    let winner: unknown = BAD,
      best = -1;
    const errors: Issue[] = [];
    const known = class_name(value);
    const options = branches(schema);
    const selected = known ? options.find((branch) => branch.name === known) : undefined;
    for (const branch of options) {
      if (selected && selected !== branch) continue;
      const issues: Issue[] = [];
      loc.push(branch.label);
      const result = node(branch.schema, value, { ...ctx, issues }, loc, undefined, depth);
      loc.pop();
      if (!issues.length) {
        let score = 0;
        if (record(value))
          for (const field of branch.fields) if (Object.hasOwn(value, field)) score++;
        if (score > best) {
          winner = result;
          best = score;
        }
      } else errors.push(...issues);
    }
    if (best >= 0) return winner;
    ctx.issues.push(...errors);
    return BAD;
  }
  if (schema.enum || Object.hasOwn(schema, 'const')) {
    const options = schema.enum ?? [schema.const];
    if (options.includes(value)) return value;
    const enumeration = schema['x-python-enum'];
    return fail(
      ctx,
      enumeration ? 'enum' : 'literal_error',
      `Input should be ${expected(options)}`,
      value,
      loc,
    );
  }
  if (schema.type === 'array') {
    if (
      !Array.isArray(value) ||
      (ctx.strict &&
        ctx.pythonInput &&
        schema['x-python-container'] === 'tuple' &&
        !tuple_value(value))
    )
      return fail(
        ctx,
        schema['x-python-container'] === 'tuple' ? 'tuple_type' : 'list_type',
        `Input should be a valid ${ctx.origin === 'json' ? 'array' : schema['x-python-container'] === 'tuple' ? 'tuple' : 'list'}`,
        value,
        loc,
      );
    const items = value;
    if (ctx.revalidate && items.length && depth >= EXECUTOR_JSON_MAX_DEPTH)
      throw new ValueError('recursion limit exceeded');
    const result: unknown[] = [];
    const prefix = schema.prefixItems;
    if (prefix) {
      prefix.forEach((item, i) => {
        if (i >= items.length) fail(ctx, 'missing', 'Field required', items, [...loc, i]);
        else {
          loc.push(i);
          result.push(node(item, items[i], ctx, loc, undefined, depth + 1));
          loc.pop();
        }
      });
      if (schema.maxItems !== undefined && value.length > schema.maxItems)
        fail(
          ctx,
          'too_long',
          `Tuple should have at most ${schema.maxItems} items after validation, not ${value.length}`,
          value,
          loc,
        );
    } else {
      for (let i = 0; i < items.length; i++) {
        loc.push(i);
        result.push(node(schema.items!, items[i], ctx, loc, undefined, depth + 1));
        loc.pop();
      }
    }
    if (schema['x-python-container'] === 'tuple') tag_tuple(result);
    return result;
  }
  if (schema.type === 'object') {
    const fields = schema['x-python-fields'];
    const known = class_name(value);
    if (fields && known === name && !ctx.revalidate) return value;
    if (!record(value) && !(fields && known === name && ctx.revalidate))
      return fail(
        ctx,
        fields ? 'dataclass_type' : 'dict_type',
        fields
          ? ctx.origin === 'json'
            ? 'Input should be an object'
            : `Input should be a dictionary or an instance of ${name}`
          : ctx.origin === 'json'
            ? 'Input should be an object'
            : 'Input should be a valid dictionary',
        value,
        loc,
      );
    const original = value;
    let object = value as Record<string, unknown>;
    if (
      ctx.revalidate &&
      depth >= EXECUTOR_JSON_MAX_DEPTH &&
      (fields ? fields.length : Object.keys(object).length)
    )
      throw new ValueError('recursion limit exceeded');
    if (name === 'ChildCommand') {
      for (const key of ['operation', 'arguments'])
        if (!Object.hasOwn(object, key)) throw new KeyError(key);
      const operation = object.operation;
      if (!definitions.ChildOperation!.enum!.includes(operation))
        return fail(
          ctx,
          'value_error',
          `Value error, ${python_repr(operation)} is not a valid ChildOperation`,
          original,
          loc,
        );
      const raw = record(object.arguments)
        ? ordered_json_object([['kind', operation], ...ordered_json_entries(object.arguments)])
        : object.arguments;
      const count = ctx.issues.length;
      const arguments_value = node(
        definitions.ChildCommandArguments!,
        raw,
        { ...ctx, strict: false, pythonInput: true },
        loc,
        undefined,
        depth + 1,
      );
      if (ctx.issues.length > count) return BAD;
      if ((arguments_value as Record<string, unknown>).kind !== operation)
        return fail(
          ctx,
          'value_error',
          'Value error, command operation conflicts with arguments',
          original,
          loc,
        );
      object = ordered_json_object(
        ordered_json_entries(object).map(([k, v]) => [k, k === 'arguments' ? arguments_value : v]),
      );
      ctx = { ...ctx, pythonInput: true };
    }
    if (!fields) {
      const entries: [string, unknown][] = [];
      for (const [key, item] of ordered_json_entries(object)) {
        loc.push(key);
        entries.push([
          key,
          typeof schema.additionalProperties === 'object'
            ? node(schema.additionalProperties, item, ctx, loc, undefined, depth + 1)
            : item,
        ]);
        loc.pop();
      }
      return ordered_json_object(entries);
    }
    const count = ctx.issues.length;
    const result: Record<string, unknown> = Object.create(null);
    for (const key of fields) {
      const child = schema.properties![key]!;
      if (Object.hasOwn(object, key) && object[key] !== undefined) {
        loc.push(key);
        result[key] = node(
          child,
          object[key],
          name === 'ChildCommand' && key === 'arguments' ? { ...ctx, revalidate: false } : ctx,
          loc,
          undefined,
          depth + 1,
        );
        loc.pop();
      } else if (Object.hasOwn(child, 'default')) result[key] = default_value(child);
      else fail(ctx, 'missing', 'Field required', object, [...loc, key]);
    }
    if (schema.additionalProperties === false)
      for (const [key, v] of ordered_json_entries(object))
        if (!fields.includes(key))
          fail(ctx, 'unexpected_keyword_argument', 'Unexpected keyword argument', v, [...loc, key]);
    if (ctx.issues.length !== count) return BAD;
    tag_class(result, name!, representations(schema));
    let problem: string | undefined;
    if (name === 'ChildCommand') {
      const args = result.arguments as Record<string, unknown>;
      if (['write_prompt', 'steer'].includes(String(args.kind)) && result.turn === null)
        problem = 'prompt command needs its exact turn';
      if (args.answer) {
        const answer = args.answer as Record<string, unknown>,
          ask = answer.ask as Record<string, unknown>;
        const equal = (a: unknown, b: unknown) =>
          stringify_pydantic_json(a) === stringify_pydantic_json(b);
        if (
          answer.answer_id !== result.command_id ||
          !equal(ask.child, result.child) ||
          !equal(ask.turn, result.turn) ||
          (args.kind === 'answer_permission_ask'
            ? ask.kind !== 'permission' ||
              ask.ask_id !== args.ask_id ||
              answer.option_id !== args.option_id ||
              answer.user_input_answers !== null
            : ask.kind !== 'user_input' ||
              ask.ask_id !== args.request_id ||
              !equal(answer.user_input_answers, args.answers) ||
              answer.option_id !== null)
        )
          problem = 'durable answer conflicts with command';
      }
    }
    if (name === 'MailboxCommand') {
      if ((result.child_command === null) === (result.model_list_request === null))
        problem = 'a mailbox command carries exactly one payload';
      else if (
        result.child_command &&
        (result.child_command as Record<string, unknown>).command_id !== result.command_id
      )
        problem = 'mailbox command id conflicts with its child command';
    }
    return problem ? fail(ctx, 'value_error', 'Value error, ' + problem, original, loc) : result;
  }
  return scalar(schema, value, ctx, loc);
}
export function decode_json_value<K extends WireRootName>(
  value: unknown,
  root: K,
  strict = false,
  origin: 'json' | 'python' = 'json',
  revalidate = false,
): WireTypes[K] {
  const ctx: Context = { strict, origin, pythonInput: origin === 'python', issues: [], revalidate };
  const result = node(definitions[root]!, value, ctx, [], root);
  if (ctx.issues.length) throw new ValidationError(root, ctx.issues, ERROR_VERSION);
  return result as WireTypes[K];
}
export function decode_value<K extends WireRootName>(value: unknown, root: K): WireTypes[K] {
  return decode_json_value(value, root, false, 'python');
}
function encode_node(value: unknown, schema?: Schema): unknown {
  if (schema?.$ref === '#/$defs/JsonValue') schema = undefined;
  if (schema) schema = dereference(schema);
  if (schema?.anyOf) {
    const choices = branches(schema);
    const known = class_name(value);
    schema =
      value === null
        ? schema.anyOf.find((s) => s.type === 'null')
        : (choices.find((branch) => branch.name === known && known !== undefined)?.schema ??
          schema.anyOf.find((s) => s.type !== 'null'));
    if (schema) schema = dereference(schema);
  }
  if (schema?.oneOf) {
    const kind =
      value !== null && typeof value === 'object'
        ? (value as Record<string, unknown>).kind
        : undefined;
    schema = dereference(
      schema.oneOf.find((branch) => dereference(branch).properties?.kind?.const === kind) ??
        schema.oneOf[0]!,
    );
  }
  if (value instanceof JsonNumber) {
    if (value.kind === 'integer' && String(value.integer).length > EXECUTOR_JSON_MAX_INTEGER_LENGTH)
      throw new ValueError('integer exceeds JSON number limit');
    return !Number.isFinite(value.value) && value.kind === 'float' ? null : value;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    if (schema?.type === 'number')
      return new JsonNumber(
        Object.is(value, -0)
          ? '-0.0'
          : String(value) +
              (!String(value).includes('.') && !String(value).includes('e') ? '.0' : ''),
      );
    return value;
  }
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (Array.isArray(value))
    return value.map((v, i) => encode_node(v, schema?.prefixItems?.[i] ?? schema?.items));
  if (value instanceof Uint8Array)
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(value);
  if (typeof value === 'object') {
    const fields = schema?.['x-python-fields'];
    const record = value as Record<string, unknown>;
    const entries: [string, unknown][] = [];
    if (fields) {
      for (const key of fields)
        entries.push([key, encode_node(record[key], schema!.properties![key])]);
    } else {
      const child =
        typeof schema?.additionalProperties === 'object' ? schema.additionalProperties : undefined;
      for (const [key, item] of ordered_json_entries(record))
        entries.push([key, encode_node(item, child)]);
    }
    return ordered_json_object(entries);
  }
  throw new TypeError('Unable to serialize executor value');
}
function validated_output(value: unknown, root: WireRootName, strict: boolean): JsonValue {
  const normalized = decode_json_value(value, root, false, 'python', true);
  const encoded = encode_node(normalized, definitions[root]) as JsonValue;
  // JSON conversion maps nonfinite values to null. Check what the peer will actually read.
  decode_json_value(encoded, root, strict, 'json', true);
  return encoded;
}
export function encode_value<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): JsonValue {
  return validated_output(value, root, false);
}
export function encode_typed_value<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
  operation: 'bounded' | 'frame',
): JsonValue {
  return validated_output(value, root, operation === 'frame');
}
