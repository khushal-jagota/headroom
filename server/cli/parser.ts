import { close_matches } from './suggestions.ts';
import { statSync } from 'node:fs';
import { resolve } from 'node:path';
import type { CommandDefinition, Parameter } from './contracts.ts';
import { command_path, command_shape, usage_error } from './errors.ts';

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
    const point = character.codePointAt(0)!;
    let start = point;
    while (start > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(start - 1))) start--;
    ascii += (point - start) % 10;
  }
  return BigInt(ascii);
}
function converted(command: CommandDefinition, p: Parameter, text: string): unknown {
  let message: string | undefined;
  if (p.type === 'choice' && !p.choices?.includes(text))
    message = `'${text}' is not one of ${(p.choices ?? []).map((s) => `'${s}'`).join(', ')}.`;
  if (p.type === 'integer' || p.type === 'float') {
    const value = p.type === 'integer' ? integer_text(text) : Number(text);
    const valid =
      value !== null && (p.type === 'integer' || (text.trim() !== '' && !Number.isNaN(value)));
    const type_name = p.type + (p.min !== undefined || p.max !== undefined ? ' range' : '');
    if (!valid) message = `'${text}' is not a valid ${type_name}.`;
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
    if (['1', 'true', 't', 'yes', 'y', 'on'].includes(text.toLowerCase())) return true;
    if (['0', 'false', 'f', 'no', 'n', 'off'].includes(text.toLowerCase())) return false;
    message = `'${text}' is not a valid boolean.`;
  }
  if (p.type === 'path') {
    let stat;
    try {
      stat = statSync(resolve(text));
    } catch {
      if (p.exists) message = `Path '${text}' does not exist.`;
    }
    if (stat?.isFile() && p.file_okay === false) message = `Directory '${text}' is a file.`;
    if (stat?.isDirectory() && p.dir_okay === false) message = `File '${text}' is a directory.`;
  }
  if (message) {
    const label = p.argument ? p.metavar : (p.opts ?? []).join(' / ');
    usage_error(command, `Invalid value for '${label}': ${message}`);
  }
  return text;
}
export interface Parsed {
  command: CommandDefinition;
  values: Record<string, any>;
  help: boolean;
}
export function parse(argv: string[], commands: readonly CommandDefinition[]): Parsed {
  let command = commands.find((c) => !c.path.length)!;
  let tokens = [...argv];
  while (command.group) {
    if (!tokens.length)
      usage_error(command, `${command_path(command)} needs a command.`, {
        no_route: 'No panels call can be named until an action is selected.',
      });
    const token = tokens.shift()!;
    if (token === '--help') return { command, values: {}, help: true };
    if (token.startsWith('-'))
      usage_error(
        command,
        `"${token.split('=')[0]}" is not an option for ${command_path(command)}.`,
        { no_route: 'No panels call can be named until an action is selected.' },
      );
    const child = commands.find((c) => c.path.join(' ') === [...command.path, token].join(' '));
    if (!child) {
      if (
        (argv[0] === 'day' && ['set', 'add-ticket', 'remove-ticket'].includes(argv[1])) ||
        (argv[0] === 'sprint' &&
          ['create', 'set', 'add-item', 'remove-item', 'carry-item', 'outcome'].includes(argv[1]))
      )
        usage_error(command, 'Calendar planning writes are retired.', {
          recovery: argv[0] === 'day' ? 'panels day show DATE' : 'panels sprint list',
        });
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
      const options =
        argv.slice(0, 2).join(' ') === 'manager page'
          ? { recovery: 'panels page --help' }
          : suggestedChild
            ? { recovery: command_shape(suggestedChild) }
            : { no_route: `No replacement panels command can be inferred for "${token}".` };
      usage_error(command, `"${token}" is not a command under ${command_path(command)}.`, options);
    }
    command = child;
  }
  const values: Record<string, any> = {};
  for (const p of command.parameters)
    values[p.name] =
      p.nargs === -1
        ? []
        : p.multiple
          ? [...((p.default as unknown[]) ?? [])]
          : Object.hasOwn(p, 'default')
            ? p.default
            : p.flag
              ? false
              : null;
  const positionals: string[] = [];
  let positionalOnly = false;
  while (tokens.length) {
    const token = tokens.shift()!;
    if (!positionalOnly && token === '--') {
      positionalOnly = true;
      continue;
    }
    if (!positionalOnly && token === '--help') return { command, values, help: true };
    if (!positionalOnly && token.startsWith('-') && token !== '-') {
      const equal = token.indexOf('=');
      const name = equal < 0 ? token : token.slice(0, equal);
      const p = command.parameters.find(
        (p) => p.opts?.includes(name) || p.secondary_opts?.includes(name),
      );
      if (!p)
        usage_error(command, `"${name}" is not an option for ${command_path(command)}.`, {
          recovery: command_shape(command),
        });
      if (p.flag) {
        if (equal >= 0) usage_error(command, `Option '${name}' does not take a value.`);
        values[p.name] = !p.secondary_opts?.includes(name);
      } else {
        const raw = equal >= 0 ? token.slice(equal + 1) : tokens.shift();
        if (raw === undefined) usage_error(command, `Option '${name}' requires an argument.`);
        const value = converted(command, p, raw);
        if (p.multiple) values[p.name].push(value);
        else values[p.name] = value;
      }
    } else positionals.push(token);
  }
  let index = 0;
  for (const p of command.parameters.filter((p) => p.argument)) {
    if (p.nargs === -1) {
      values[p.name] = positionals.slice(index).map((raw) => converted(command, p, raw));
      index = positionals.length;
      if (p.required && !values[p.name].length)
        usage_error(command, `Missing argument '${p.metavar}'.`);
      continue;
    }
    const raw = positionals[index++];
    if (raw !== undefined) values[p.name] = converted(command, p, raw);
    else if (p.required) usage_error(command, `Missing argument '${p.metavar}'.`);
  }
  if (positionals.length > index)
    usage_error(
      command,
      `Got unexpected extra argument${positionals.length - index > 1 ? 's' : ''} (${positionals.slice(index).join(' ')})`,
    );
  for (const p of command.parameters.filter((p) => !p.argument && p.required)) {
    if (
      values[p.name] === null ||
      values[p.name] === undefined ||
      (p.flag && values[p.name] === false)
    )
      usage_error(command, `Missing option '${p.opts?.[0]}'.`);
  }
  return { command, values, help: false };
}
