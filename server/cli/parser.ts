import { python_string_repr } from '../core/python_repr.ts';
import { close_matches } from './suggestions.ts';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import type { CommandDefinition, Parameter } from './contracts.ts';
import { command_path, command_shape, usage_error } from './errors.ts';

/** Decimal Unicode digits have the same numeric value in Python int(). */
function decimal_digit(character: string): string {
  const point = character.codePointAt(0)!;
  let start = point;
  while (start > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(start - 1))) start--;
  return String((point - start) % 10);
}
function python_strip(text: string): string {
  return text.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
}
/** Mirror uuid.UUID(hex=...) after Click strips the option value. */
function uuid_text(text: string): string | null {
  const hex = python_strip(text)
    .replaceAll('urn:', '')
    .replaceAll('uuid:', '')
    .replace(/^[{}]+|[{}]+$/g, '')
    .replaceAll('-', '');
  if ([...hex].length !== 32) return null;
  // int(hex, 16) also accepts signs, prefixes, underscores and decimal Unicode digits.
  // Its ASCII whitespace grammar excludes the extra str.strip control separators.
  const digits = hex
    .replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '')
    .replace(/\p{Decimal_Number}/gu, decimal_digit);
  if (!/^[+]?(?:0[xX]_?)?[0-9a-fA-F](?:_?[0-9a-fA-F])*$/.test(digits)) return null;
  const value = BigInt(
    '0x' +
      digits
        .replace(/^\+/, '')
        .replace(/^0[xX]/, '')
        .replaceAll('_', ''),
  );
  if (value >= 1n << 128n) return null;
  const normalized = value.toString(16).padStart(32, '0');
  return [
    normalized.slice(0, 8),
    normalized.slice(8, 12),
    normalized.slice(12, 16),
    normalized.slice(16, 20),
    normalized.slice(20),
  ].join('-');
}

/** Python int() accepts decimal Unicode digits and underscores between digits. */
function integer_text(text: string): bigint | null {
  if (text.includes('\uFEFF')) return null;
  const trimmed = text.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, '');
  if (
    trimmed.includes('\uFEFF') ||
    !/^[+-]?\p{Decimal_Number}(?:_?\p{Decimal_Number})*$/u.test(trimmed)
  )
    return null;
  let ascii = '';
  for (const character of trimmed) {
    if (character === '_') continue;
    if (character === '+' || character === '-') {
      ascii += character;
      continue;
    }
    ascii += decimal_digit(character);
  }
  return BigInt(ascii);
}
/** Resolve existing prefixes too, as os.path.realpath does for a missing final path. */
function resolved_path(text: string): string {
  try {
    return realpathSync(text);
  } catch {
    const parent = dirname(text);
    return parent === text ? resolve(text) : join(resolved_path(parent), basename(text));
  }
}
function converted(command: CommandDefinition, p: Parameter, text: string): unknown {
  let message: string | undefined;
  if (p.type === 'uuid') {
    const value = uuid_text(text);
    if (value !== null) return value;
    message = `${python_string_repr(python_strip(text))} is not a valid UUID.`;
  }
  if (p.type === 'choice' && !p.choices?.includes(text))
    message = `${python_string_repr(text)} is not${p.choices?.length === 1 ? '' : ' one of'} ${(p.choices ?? []).map(python_string_repr).join(', ')}.`;
  if (p.type === 'integer' || p.type === 'float') {
    const value = p.type === 'integer' ? integer_text(text) : Number(text);
    const valid =
      value !== null && (p.type === 'integer' || (text.trim() !== '' && !Number.isNaN(value)));
    const type_name = p.type + (p.min !== undefined || p.max !== undefined ? ' range' : '');
    if (!valid) message = `${python_string_repr(text)} is not a valid ${type_name}.`;
    else if ((p.min !== undefined && value < p.min) || (p.max !== undefined && value > p.max)) {
      const range =
        p.min !== undefined && p.max !== undefined
          ? `${p.min}<=x<=${p.max}`
          : p.min !== undefined
            ? `x>=${p.min}`
            : `x<=${p.max}`;
      message = `${value} is not in the range ${range}.`;
    } else if (typeof value === 'bigint') {
      return value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER)
        ? Number(value)
        : value;
    } else return value;
  }
  if (p.type === 'boolean') {
    if (
      ['1', 'true', 't', 'yes', 'y', 'on'].includes(
        text
          .replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '')
          .toLowerCase(),
      )
    )
      return true;
    if (
      ['0', 'false', 'f', 'no', 'n', 'off'].includes(
        text
          .replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '')
          .toLowerCase(),
      )
    )
      return false;
    message = `${python_string_repr(text)} is not a valid boolean. Recognized values: 0, 1, f, false, n, no, off, on, t, true, y, yes`;
  }
  if (p.type === 'path') {
    if (p.allow_dash && p.file_okay && text === '-') return text;
    const value = p.resolve_path ? resolved_path(text) : text;
    const name =
      p.file_okay && !p.dir_okay ? 'File' : p.dir_okay && !p.file_okay ? 'Directory' : 'Path';
    let stat;
    try {
      stat = statSync(value);
    } catch {
      if (p.exists) message = `${name} ${python_string_repr(text)} does not exist.`;
    }
    if (stat) {
      if (stat.isFile() && p.file_okay === false)
        message = `${name} ${python_string_repr(text)} is a file.`;
      if (stat.isDirectory() && p.dir_okay === false)
        message = `${name} ${python_string_repr(text)} is a directory.`;
      for (const [enabled, permission, adjective] of [
        [p.readable, constants.R_OK, 'readable'],
        [p.writable, constants.W_OK, 'writable'],
        [p.executable, constants.X_OK, 'executable'],
      ] as const) {
        if (enabled && !message) {
          try {
            accessSync(value, permission);
          } catch {
            message = `${name} ${python_string_repr(text)} is not ${adjective}.`;
          }
        }
      }
    }
    if (!message) return value;
  }
  if (message) {
    const label = p.argument ? p.metavar : (p.opts ?? []).join(' / ');
    usage_error(command, `Invalid value for ${python_string_repr(label ?? '')}: ${message}`);
  }
  return text;
}
/** Choice parameters append their available values to Click's missing-value error. */
function missing_message(p: Parameter): string {
  const label = p.argument ? p.metavar : p.opts?.[0];
  const base = `Missing ${p.argument ? 'argument' : 'option'} '${label}'.`;
  return p.type === 'choice' ? base + ' Choose from: ' + (p.choices ?? []).join(', ') : base;
}
export interface Parsed {
  command: CommandDefinition;
  values: Record<string, any>;
  help: boolean;
}
/** Python's callback-level Ticket fallback also applies where Click declares no envvar. */
export function ticket_argument(
  positional: string | null | undefined,
  environment: NodeJS.ProcessEnv,
): string | undefined {
  if (positional !== null && positional !== undefined && positional !== '-') return positional;
  const value = python_strip(environment.PLAN_TICKET_ID ?? '');
  return value && value !== '-' ? value : undefined;
}
/** Resolve only declared envvars. Empty values count as absent, as in Click. */
export function parameter_environment(
  parameter: Parameter,
  environment: NodeJS.ProcessEnv,
): string | undefined {
  for (const name of typeof parameter.envvar === 'string'
    ? [parameter.envvar]
    : (parameter.envvar ?? [])) {
    const value = environment[name];
    if (value) return value;
  }
  return undefined;
}
export function parse(
  argv: string[],
  commands: readonly CommandDefinition[],
  environment: NodeJS.ProcessEnv = process.env,
): Parsed {
  let command = commands.find((c) => !c.path.length)!;
  let tokens = [...argv];
  while (command.group) {
    if (!tokens.length)
      usage_error(command, `${command_path(command)} needs a command.`, {
        no_route: 'No panels call can be named until an action is selected.',
      });
    // Group parsing stops at the first command token. Eager help runs only after
    // this option prefix is syntactically valid, before command resolution.
    let help = false;
    while (tokens.length) {
      const option = tokens[0];
      if (option === '--') {
        tokens.shift();
        break;
      }
      if (!option.startsWith('-') || option === '-') break;
      tokens.shift();
      if (option === '--help') {
        help = true;
        continue;
      }
      if (option.startsWith('--help='))
        usage_error(command, "Option '--help' does not take a value.", { recovery: null });
      const name = option.startsWith('--') ? option.split('=')[0] : option.slice(0, 2);
      usage_error(command, `"${name}" is not an option for ${command_path(command)}.`, {
        no_route: 'No panels call can be named until an action is selected.',
      });
    }
    if (help) return { command, values: {}, help: true };
    if (!tokens.length) usage_error(command, 'Missing command.');
    const token = tokens.shift()!;
    const child = commands.find((c) => c.path.join(' ') === [...command.path, token].join(' '));
    if (!child) {
      const retiredDay =
        argv[0] === 'day' && ['set', 'add-ticket', 'remove-ticket'].includes(argv[1]);
      const retiredSprint =
        argv[0] === 'sprint' &&
        (['create', 'set', 'add-item', 'remove-item', 'carry-item'].includes(argv[1]) ||
          (argv[1] === 'outcome' && ['add', 'remove', 'carry'].includes(argv[2])));
      if (retiredDay || retiredSprint)
        usage_error(command, 'Calendar planning writes are retired.', {
          recovery: retiredDay ? 'panels day show DATE' : 'panels sprint list',
        });
      // Click resolves an option-looking unknown command by parsing it again.
      if (token.startsWith('-') && token !== '-') {
        if (token === '--help') return { command, values: {}, help: true };
        if (token.startsWith('--help='))
          usage_error(command, "Option '--help' does not take a value.", { recovery: null });
        const name = token.startsWith('--') ? token.split('=')[0] : token.slice(0, 2);
        usage_error(command, `"${name}" is not an option for ${command_path(command)}.`, {
          no_route: 'No panels call can be named until an action is selected.',
        });
      }
      const children = commands.filter(
        (c) =>
          c.path.length === command.path.length + 1 &&
          c.path.slice(0, -1).join(' ') === command.path.join(' '),
      );
      const suggested = close_matches(
        token,
        children.map((c) => c.path.at(-1)!),
      )[0];
      const suggestedChild = children.find((c) => c.path.at(-1) === suggested);
      usage_error(
        command,
        `"${token}" is not a command under ${command_path(command)}.`,
        argv.slice(0, 2).join(' ') === 'manager page'
          ? { recovery: 'panels page --help' }
          : suggestedChild
            ? { recovery: command_shape(suggestedChild) }
            : { no_route: `No replacement panels command can be inferred for "${token}".` },
      );
    }
    command = child;
  }
  // First collect raw tokens. No conversion or eager callback runs until the
  // entire leaf line passes option syntax, matching Click's parser boundary.
  const raw = new Map<Parameter, unknown>();
  const order: Parameter[] = [];
  const positionals: string[] = [];
  let positionalOnly = false;
  let help = false;
  while (tokens.length) {
    const token = tokens.shift()!;
    if (!positionalOnly && token === '--') {
      positionalOnly = true;
      continue;
    }
    if (!positionalOnly && token === '--help') {
      help = true;
      continue;
    }
    if (!positionalOnly && token.startsWith('--help='))
      usage_error(command, "Option '--help' does not take a value.", { recovery: null });
    if (!positionalOnly && token.startsWith('-') && token !== '-') {
      const equal = token.indexOf('=');
      const name = token.startsWith('--')
        ? equal < 0
          ? token
          : token.slice(0, equal)
        : token.slice(0, 2);
      const parameter = command.parameters.find(
        (p) => p.opts?.includes(name) || p.secondary_opts?.includes(name),
      );
      if (!parameter)
        usage_error(command, `"${name}" is not an option for ${command_path(command)}.`, {
          recovery: command_shape(command),
        });
      order.push(parameter);
      if (parameter.flag) {
        if (equal >= 0)
          usage_error(command, `Option '${name}' does not take a value.`, { recovery: null });
        raw.set(parameter, !parameter.secondary_opts?.includes(name));
      } else {
        const value = equal >= 0 ? token.slice(equal + 1) : tokens.shift();
        if (value === undefined)
          usage_error(command, `Option '${name}' requires an argument.`, { recovery: null });
        if (parameter.multiple)
          raw.set(parameter, [...((raw.get(parameter) as string[]) ?? []), value]);
        else raw.set(parameter, value);
      }
    } else positionals.push(token);
  }
  let index = 0;
  for (const parameter of command.parameters.filter((p) => p.argument)) {
    order.push(parameter);
    if (parameter.nargs === -1) {
      const values = positionals.slice(index);
      index = positionals.length;
      if (values.length) raw.set(parameter, values);
    } else if (index < positionals.length) raw.set(parameter, positionals[index++]);
  }
  if (help) return { command, values: {}, help: true };
  const values: Record<string, any> = {};
  const parameters = [...command.parameters].sort((a, b) => {
    const left = order.indexOf(a),
      right = order.indexOf(b);
    return (left < 0 ? Infinity : left) - (right < 0 ? Infinity : right);
  });
  for (const parameter of parameters) {
    let value = raw.get(parameter);
    if (value === undefined) {
      const env = parameter_environment(parameter, environment);
      value =
        env === undefined
          ? parameter.default
          : parameter.nargs === -1 || parameter.multiple
            ? env.split(/\s+/u).filter(Boolean)
            : env;
    }
    if (value === undefined || (value === null && (parameter.nargs === -1 || parameter.multiple)))
      value = parameter.nargs === -1 || parameter.multiple ? [] : parameter.flag ? false : null;
    if (value !== null) {
      if (parameter.nargs === -1 || parameter.multiple)
        value = (value as string[]).map((item) => converted(command, parameter, item));
      else if (!(parameter.flag && typeof value === 'boolean'))
        value = converted(command, parameter, String(value));
    }
    if (parameter.required && (value === null || (Array.isArray(value) && !value.length)))
      usage_error(command, missing_message(parameter));
    values[parameter.name] = value;
  }
  if (positionals.length > index)
    usage_error(
      command,
      `Got unexpected extra argument${positionals.length - index > 1 ? 's' : ''} (${positionals.slice(index).join(' ')})`,
    );
  return { command, values, help: false };
}
