/** Typed native declarations shared by the parser and help. */
import { accessSync, constants, statSync } from 'node:fs';
import { Argument, Command, Help, InvalidArgumentError, Option } from 'commander';

export interface ValueParser {
  (text: string): unknown;
  kind: 'string' | 'integer' | 'boolean' | 'choice' | 'path' | 'uuid';
  choices?: string[];
  min?: number;
  max?: number;
  pathKind?: 'any' | 'file' | 'directory';
  mustExist?: boolean;
}
export const text: ValueParser = Object.assign((value: string) => value, {
  kind: 'string' as const,
});
export const boolean: ValueParser = Object.assign(
  (value: string) => {
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 't', 'yes', 'y', 'on'].includes(normalized)) return true;
    if (['0', 'false', 'f', 'no', 'n', 'off'].includes(normalized)) return false;
    throw new InvalidArgumentError(`Expected a boolean, received ${JSON.stringify(value)}.`);
  },
  { kind: 'boolean' as const },
);
export const uuid: ValueParser = Object.assign(
  (value: string) => {
    const hex = value
      .trim()
      .replace(/^urn:uuid:/i, '')
      .replace(/^\{(.*)\}$/, '$1')
      .replaceAll('-', '');
    if (!/^[0-9a-f]{32}$/i.test(hex))
      throw new InvalidArgumentError(`Expected a UUID, received ${JSON.stringify(value)}.`);
    return hex.toLowerCase().replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, '$1-$2-$3-$4-$5');
  },
  { kind: 'uuid' as const },
);
export function integer(min?: number, max?: number): ValueParser {
  return Object.assign(
    (value: string) => {
      if (!/^[+-]?[0-9]+$/.test(value.trim()))
        throw new InvalidArgumentError(
          `Expected a decimal integer, received ${JSON.stringify(value)}.`,
        );
      const exact = BigInt(value.trim());
      if ((min !== undefined && exact < min) || (max !== undefined && exact > max))
        throw new InvalidArgumentError(
          `Expected an integer between ${min ?? '-Infinity'} and ${max ?? 'Infinity'}.`,
        );
      return exact >= BigInt(Number.MIN_SAFE_INTEGER) && exact <= BigInt(Number.MAX_SAFE_INTEGER)
        ? Number(exact)
        : exact;
    },
    { kind: 'integer' as const, min, max },
  );
}
export function choice(choices: string[]): ValueParser {
  return Object.assign(
    (value: string) => {
      if (!choices.includes(value))
        throw new InvalidArgumentError(
          `Expected one of ${choices.join(', ')}, received ${JSON.stringify(value)}.`,
        );
      return value;
    },
    { kind: 'choice' as const, choices },
  );
}
export function readable_path(
  pathKind: 'any' | 'file' | 'directory',
  mustExist: boolean,
): ValueParser {
  return Object.assign(
    (value: string) => {
      let stat;
      try {
        stat = statSync(value);
      } catch {
        if (mustExist)
          throw new InvalidArgumentError(
            `The ${pathKind === 'any' ? 'path' : pathKind} does not exist: ${value}`,
          );
        return value;
      }
      if (
        (pathKind === 'file' && stat.isDirectory()) ||
        (pathKind === 'directory' && stat.isFile())
      )
        throw new InvalidArgumentError(`Expected a ${pathKind}: ${value}`);
      try {
        accessSync(value, constants.R_OK);
      } catch {
        throw new InvalidArgumentError(`The path is not readable: ${value}`);
      }
      return value;
    },
    { kind: 'path' as const, pathKind, mustExist },
  );
}
interface InputSettings {
  default?: unknown;
  description?: string;
  required?: boolean;
  repeat?: boolean;
  hidden?: boolean;
  inverse?: boolean;
  envNames?: string[];
}
export class CliOption extends Option {
  readonly key: string;
  readonly convert: ValueParser;
  readonly repeat: boolean;
  readonly inverse: boolean;
  constructor(key: string, flags: string, convert: ValueParser, settings: InputSettings) {
    super(flags, settings.description);
    this.key = key;
    this.convert = convert;
    this.repeat = settings.repeat ?? false;
    this.inverse = settings.inverse ?? false;
    this.makeOptionMandatory(settings.required ?? false);
    if (!settings.required) this.default(settings.default ?? null);
    if (settings.hidden) this.hideHelp();
    if (this.inverse) this.preset(false);
    if (this.repeat)
      this.argParser((value: string, previous: string[] = []) => [...(previous ?? []), value]);
  }
  override attributeName(): string {
    return this.key;
  }
}
export class CliArgument extends Argument {
  readonly key: string;
  readonly convert: ValueParser;
  readonly envNames: string[];
  constructor(key: string, syntax: string, convert: ValueParser, settings: InputSettings) {
    super(syntax, settings.description);
    this.key = key;
    this.convert = convert;
    this.envNames = settings.envNames ?? [];
    if (!this.required) this.default(settings.default ?? null);
  }
}
export const option = (
  key: string,
  flags: string,
  convert: ValueParser = text,
  settings: InputSettings = {},
) => new CliOption(key, flags, convert, settings);
export const argument = (
  key: string,
  syntax: string,
  convert: ValueParser = text,
  settings: InputSettings = {},
) => new CliArgument(key, syntax, convert, settings);
export class CliCommand extends Command {
  readonly path: string[];
  readonly inputs: (CliOption | CliArgument)[] = [];
  // Commander owns suggestions; use its diagnostics before eager help.
  declare unknownCommand: () => never;
  declare unknownOption: (flag: string) => never;
  constructor(path: string[], description: string) {
    super(path.at(-1) ?? 'panels');
    this.path = path;
    this.description(description.replaceAll('\b', ''))
      .helpOption('--help', 'Show command help.')
      .helpCommand(false)
      .enablePositionalOptions()
      .allowExcessArguments(false)
      .configureHelp({
        optionDescription(this: Help, option: Option) {
          const description = Help.prototype.optionDescription.call(this, option);
          return option.isBoolean() && option.defaultValue === false
            ? description.replace(/\s*\(default: false\)$/, '')
            : description;
        },
      });
  }
  override parseOptions(argv: string[]): { operands: string[]; unknown: string[] } {
    let tokens = argv;
    if (this.commands.length) {
      // A group terminator ends that group's options. The selected child still
      // parses its own options, rather than inheriting Commander's literal tail.
      const boundary = argv.findIndex(
        (token) => token === '--' || !token.startsWith('-') || token === '-',
      );
      if (boundary >= 0 && argv[boundary] === '--')
        tokens = [...argv.slice(0, boundary), ...argv.slice(boundary + 1)];
    }
    const parsed = super.parseOptions(tokens);
    // The native parser delegates a recognized child's remaining tokens.
    if (this.commands.length && parsed.operands.length) {
      if (!this.commands.some((child) => child.name() === parsed.operands[0])) {
        this.args = parsed.operands;
        this.unknownCommand();
      }
      return parsed;
    }
    // Check native unrecognized syntax before Commander's eager help.
    for (const token of parsed.unknown) {
      if (token === '--' || (this.commands.length && (!token.startsWith('-') || token === '-')))
        break;
      if (token !== '--help' && token.startsWith('-') && token !== '-') this.unknownOption(token);
    }
    if (!this.commands.length) {
      // Commander accepts negative numeric operands. Panels requires an explicit
      // terminator for every dash-prefixed operand. Required option values remain
      // native option values, including a literal '--'.
      for (let index = 0; index < tokens.length; index++) {
        const token = tokens[index];
        if (token === '--') break;
        const option = this.options.find(
          (candidate) => candidate.short === token || candidate.long === token,
        );
        if (option) {
          if (option.required) index++;
          continue;
        }
        if (token === '--help') continue;
        if (token !== '-' && token.startsWith('-') && parsed.operands.includes(token))
          this.unknownOption(token);
      }
    }
    return parsed;
  }
  addInputs(inputs: (CliOption | CliArgument)[], environment: NodeJS.ProcessEnv): this {
    for (const input of inputs) {
      this.inputs.push(input);
      if (input instanceof CliOption) this.addOption(input);
      else {
        const value = input.envNames.map((name) => environment[name]).find(Boolean);
        if (value !== undefined)
          input.default(input.variadic ? value.split(/\s+/u).filter(Boolean) : value);
        this.addArgument(input);
      }
    }
    return this;
  }
}
export function find_command(root: CliCommand, path: readonly string[]): CliCommand | undefined {
  let current: CliCommand | undefined = root;
  for (const name of path)
    current = current?.commands.find((command) => command.name() === name) as
      CliCommand | undefined;
  return current;
}
