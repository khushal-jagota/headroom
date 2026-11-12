/** The current executor schema grammar, source validation modes and value serializer. */
import { readFileSync } from 'node:fs';
import { pure_posix_path } from '../core/python_path.ts';
import { JsonNumber, numeric_value, stringify_pydantic_json } from '../core/http/numeric_json.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type { JsonValue, WireRootName, WireTypes } from './wire.generated.ts';
import { ordered_json_entries, ordered_json_object } from './protocol_json.ts';
import {
  class_name,
  tuple_value,
  tag_class,
  tag_tuple,
  ValidationError,
  KeyError,
  python_repr,
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
const document = JSON.parse(
  readFileSync(new URL('./wire.schema.json', import.meta.url), 'utf8'),
) as {
  $defs: Record<string, Schema>;
  pydantic_error_version: string;
};
export const ERROR_VERSION = document.pydantic_error_version;
const definitions = document.$defs;
const BAD = Symbol('invalid executor value');
type Context = {
  strict: boolean;
  origin: 'json' | 'python';
  pythonInput: boolean;
  issues: Issue[];
};
const record = (v: unknown): v is Record<string, unknown> =>
  v !== null &&
  typeof v === 'object' &&
  !Array.isArray(v) &&
  !(v instanceof JsonNumber) &&
  !(v instanceof Uint8Array) &&
  !class_name(v);
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
  ctx.issues.push({ type, message, input, loc, url });
  return BAD;
}
function field_repr(name: string, schema: Schema): FieldRepresentation {
  schema = dereference(schema);
  if (schema.anyOf) schema = dereference(schema.anyOf.find((s) => s.type !== 'null')!);
  return { name, enumeration: schema['x-python-enum'], path: schema.format === 'path' };
}
function default_value(schema: Schema): unknown {
  const value = schema.default;
  const node = dereference(schema.anyOf?.find((s) => s.type !== 'null') ?? schema);
  if (Array.isArray(value)) {
    const result = value.slice();
    if (node['x-python-container'] === 'tuple') tag_tuple(result);
    return result;
  }
  if (typeof value === 'number')
    return new JsonNumber(
      String(value) + (node.type === 'number' && Number.isInteger(value) ? '.0' : ''),
    );
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
    if (value instanceof JsonNumber && value.kind === 'integer') return value;
    if (typeof value === 'number' && Number.isInteger(value) && !Object.is(value, -0))
      return new JsonNumber(String(BigInt(value)));
    if (!ctx.strict) {
      if (typeof value === 'boolean') return new JsonNumber(value ? '1' : '0');
      if (numeric !== undefined) {
        if (!Number.isFinite(numeric))
          return fail(ctx, 'finite_number', 'Input should be a finite number', value, loc);
        if (Number.isInteger(numeric)) return new JsonNumber(String(BigInt(numeric)));
        return fail(
          ctx,
          'int_from_float',
          'Input should be a valid integer, got a number with a fractional part',
          value,
          loc,
        );
      }
      if (typeof value === 'string') {
        const text = value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
        if (/^[+-]?[0-9]+(?:_[0-9]+)*(?:\.0+)?$/.test(text)) {
          const integer = text.replaceAll('_', '').replace(/\.0+$/, '');
          if (integer.length <= 4300) return new JsonNumber(String(BigInt(integer)));
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
      if (!Number.isFinite(numeric)) return numeric;
      return value instanceof JsonNumber && value.kind === 'float'
        ? value
        : new JsonNumber(
            Object.is(numeric, -0) && !(value instanceof JsonNumber)
              ? '-0.0'
              : String(numeric) +
                  (!String(numeric).includes('.') && !String(numeric).includes('e') ? '.0' : ''),
          );
    }
    if (!ctx.strict && typeof value === 'boolean') return new JsonNumber(value ? '1.0' : '0.0');
    if (!ctx.strict && typeof value === 'string') {
      const raw = value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
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
        return Number.isFinite(n)
          ? new JsonNumber(
              Object.is(n, -0)
                ? '-0.0'
                : String(n) + (!String(n).includes('.') && !String(n).includes('e') ? '.0' : ''),
            )
          : n;
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
  loc: readonly (string | number)[],
  name?: string,
): unknown {
  if (schema.$ref) {
    const key = schema.$ref.slice('#/$defs/'.length);
    return node(definitions[key]!, value, ctx, loc, key);
  }
  if (name === 'JsonValue') {
    if (
      value === null ||
      ['string', 'boolean', 'number'].includes(typeof value) ||
      value instanceof JsonNumber
    )
      return value;
    if (Array.isArray(value))
      return value.map((v, i) =>
        node(schema.anyOf!.find((s) => s.type === 'array')!.items!, v, ctx, [...loc, i]),
      );
    if (record(value))
      return ordered_json_object(
        ordered_json_entries(value).map(([k, v]) => [
          k,
          node(schema, v, ctx, [...loc, k], 'JsonValue'),
        ]),
      );
  }
  if (schema.oneOf) {
    const tag =
      record(value) || class_name(value) ? (value as Record<string, unknown>).kind : undefined;
    const choices = schema.oneOf.map((s) => dereference(s).properties!.kind!.const as string);
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
    return node(schema.oneOf[choices.indexOf(tag)]!, value, ctx, [...loc, tag]);
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
          );
    const input = value;
    const candidates = schema.anyOf.map((branch) => {
      const issues: Issue[] = [];
      const target = dereference(branch);
      const label =
        branch.$ref && target['x-python-fields']
          ? branch.$ref.slice('#/$defs/'.length)
          : `literal[${(target.enum ?? [target.const]).map(python_repr).join(',')}]`;
      const result = node(branch, value, { ...ctx, issues }, [...loc, label]);
      return {
        result,
        issues,
        score: record(input)
          ? Object.keys(target.properties ?? {}).filter((k) => Object.hasOwn(input, k)).length
          : 0,
      };
    });
    const winners = candidates
      .filter((c) => c.issues.length === 0)
      .sort((a, b) => b.score - a.score);
    if (winners.length) return winners[0]!.result;
    ctx.issues.push(...candidates.flatMap((c) => c.issues));
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
    const result: unknown[] = [];
    const prefix = schema.prefixItems;
    if (prefix) {
      prefix.forEach((item, i) => {
        if (i >= items.length) fail(ctx, 'missing', 'Field required', items, [...loc, i]);
        else result.push(node(item, items[i], ctx, [...loc, i]));
      });
      if (schema.maxItems !== undefined && value.length > schema.maxItems)
        fail(
          ctx,
          'too_long',
          `Tuple should have at most ${schema.maxItems} items after validation, not ${value.length}`,
          value,
          loc,
        );
    } else value.forEach((item, i) => result.push(node(schema.items!, item, ctx, [...loc, i])));
    if (schema['x-python-container'] === 'tuple') tag_tuple(result);
    return result;
  }
  if (schema.type === 'object') {
    const fields = schema['x-python-fields'];
    if (fields && class_name(value) === name) return value;
    if (!record(value))
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
    let object = value;
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
    if (!fields)
      return ordered_json_object(
        ordered_json_entries(object).map(([k, v]) => [
          k,
          typeof schema.additionalProperties === 'object'
            ? node(schema.additionalProperties, v, ctx, [...loc, k])
            : v,
        ]),
      );
    const count = ctx.issues.length;
    const result: Record<string, unknown> = Object.create(null);
    for (const key of fields) {
      const child = schema.properties![key]!;
      if (Object.hasOwn(object, key)) result[key] = node(child, object[key], ctx, [...loc, key]);
      else if (Object.hasOwn(child, 'default')) result[key] = default_value(child);
      else fail(ctx, 'missing', 'Field required', object, [...loc, key]);
    }
    if (schema.additionalProperties === false)
      for (const [key, v] of ordered_json_entries(object))
        if (!fields.includes(key))
          fail(ctx, 'unexpected_keyword_argument', 'Unexpected keyword argument', v, [...loc, key]);
    if (ctx.issues.length !== count) return BAD;
    tag_class(
      result,
      name!,
      fields.map((key) => field_repr(key, schema.properties![key]!)),
    );
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
): NumericJSON<WireTypes[K]> {
  const ctx: Context = { strict, origin, pythonInput: origin === 'python', issues: [] };
  const result = node(definitions[root]!, value, ctx, [], root);
  if (ctx.issues.length) throw new ValidationError(root, ctx.issues, ERROR_VERSION);
  return result as NumericJSON<WireTypes[K]>;
}
export function decode_value<K extends WireRootName>(
  value: unknown,
  root: K,
): NumericJSON<WireTypes[K]> {
  return decode_json_value(value, root, false, 'python');
}
function encode_node(value: unknown, schema?: Schema, typed = false, nativeField = false): unknown {
  if (schema) schema = dereference(schema);
  const known = class_name(value);
  if (!schema && known) schema = definitions[known];
  if (!schema && value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const name = value.constructor?.name;
    if (name && name !== 'Object') schema = definitions[name];
  }
  if (schema?.anyOf) {
    const choices = schema.anyOf;
    schema =
      value === null
        ? choices.find((s) => s.type === 'null')
        : choices.find((s) => s.type !== 'null');
    if (choices.length > 1 && !choices.some((s) => s.type === 'null')) {
      const name =
        known ??
        (value !== null && typeof value === 'object' ? value.constructor?.name : undefined);
      schema =
        choices.find((s) => s.$ref?.endsWith('/' + name)) ??
        choices.slice().sort((a, b) => {
          const score = (s: Schema) =>
            value !== null && typeof value === 'object'
              ? Object.keys(dereference(s).properties ?? {}).filter((k) => Object.hasOwn(value, k))
                  .length
              : 0;
          return score(b) - score(a);
        })[0];
    }
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
    if (
      typed &&
      schema?.type === 'number' &&
      value.kind === 'integer' &&
      Number.isFinite(value.value)
    ) {
      const text = String(value.value);
      return new JsonNumber(text + (!text.includes('.') && !text.includes('e') ? '.0' : ''));
    }
    return !Number.isFinite(value.value) && value.kind === 'float' ? null : value;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    if ((typed || nativeField) && schema?.type === 'number')
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
    return value.map((v, i) =>
      encode_node(v, schema?.prefixItems?.[i] ?? schema?.items, typed, nativeField),
    );
  if (value instanceof Uint8Array)
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(value);
  if (typeof value === 'object') {
    const fields = schema?.['x-python-fields'];
    const record = value as Record<string, unknown>;
    const entries = fields
      ? fields.filter((k) => Object.hasOwn(record, k)).map((k) => [k, record[k]] as const)
      : ordered_json_entries(record);
    return ordered_json_object(
      entries.map(([k, v]) => [
        k,
        encode_node(
          v,
          schema?.properties?.[k] ??
            (typeof schema?.additionalProperties === 'object'
              ? schema.additionalProperties
              : undefined),
          typed,
          Boolean(
            !class_name(value) && value.constructor?.name && value.constructor.name !== 'Object',
          ),
        ),
      ]),
    );
  }
  throw new TypeError('Unable to serialize executor value');
}
export function encode_value(value: unknown, root?: WireRootName): NumericJSON<JsonValue> {
  return encode_node(value, root ? definitions[root] : undefined) as NumericJSON<JsonValue>;
}
export function encode_typed_value(value: unknown, root: WireRootName): unknown {
  return encode_node(value, definitions[root], true);
}
