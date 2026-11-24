import { CommanderError, InvalidArgumentError } from 'commander';
import type { CommandDefinition } from './contracts.ts';
import { CliArgument, CliCommand, CliOption } from './declarations.ts';
import { create_commands } from './commands.ts';
import { CliFailure, command_path, command_shape, human_error } from './errors.ts';
import { REMOVED_OPTIONS, removed_command } from './removed.ts';
import { role_card, role_of } from './role_help.ts';

export interface Parsed {
  command: CommandDefinition;
  values: Record<string, any>;
  help: string;
}
/** Resolve the Ticket from an argument or the Worker environment. */
export function ticket_argument(
  positional: string | null | undefined,
  environment: NodeJS.ProcessEnv,
): string | undefined {
  if (positional !== null && positional !== undefined && positional !== '-') return positional;
  const value = (environment.PLAN_TICKET_ID ?? '').trim();
  return value && value !== '-' ? value : undefined;
}

export function parse(
  argv: string[],
  declarations: typeof create_commands = create_commands,
  environment: NodeJS.ProcessEnv = process.env,
): Parsed {
  const root = declarations(environment);
  let current = root;
  let selected: Parsed | undefined;
  let stdout = '';
  let stderr = '';
  function configure(command: CliCommand): void {
    command.exitOverride().configureOutput({
      writeOut: (text) => {
        current = command;
        stdout += text;
      },
      writeErr: (text) => {
        current = command;
        stderr += text;
      },
    });
    // Preview commands refuse Mac execution before help or any handler starts.
    if (command.path.join(' ') === 'preview' && environment.PLAN_EXECUTOR_ROOT)
      command.hook('preSubcommand', (_parent, child) => {
        throw new CliFailure(
          human_error(
            child as CliCommand,
            'Application previews are unavailable for Tickets running on the Mac.',
          ),
        );
      });
    if (command.commands.length) {
      for (const child of command.commands) configure(child as CliCommand);
      return;
    }
    command.action(() => {
      current = command;
      const values: Record<string, any> = {};
      let index = 0;
      // Commander owns syntax and eager help. Convert admitted values only after
      // that phase, so invalid semantic values do not prevent explicit help.
      for (const input of command.inputs) {
        if (input instanceof CliOption && input.inverse) continue;
        const raw =
          input instanceof CliArgument ? command.processedArgs[index++] : command.opts()[input.key];
        const multiple = input instanceof CliArgument ? input.variadic : input.repeat;
        const value = raw ?? (multiple ? [] : null);
        if (value === null || typeof value === 'boolean') values[input.key] = value;
        else if (multiple)
          values[input.key] = value.map((item: unknown) => input.convert(String(item)));
        else values[input.key] = input.convert(String(value));
      }
      selected = { command, values, help: '' };
    });
  }
  configure(root);
  try {
    root.parse(argv, { from: 'user' });
  } catch (error) {
    if (error instanceof CommanderError && error.exitCode === 0)
      return { command: current, values: {}, help: help_for(current, stdout) };
    if (error instanceof CommanderError || error instanceof InvalidArgumentError) {
      if (error instanceof CommanderError && error.code === 'commander.unknownCommand')
        throw new CliFailure(unknown_command(current, error.message));
      if (error instanceof CommanderError && error.code === 'commander.unknownOption') {
        const flag = (
          /^error: unknown option '(.+)'/.exec(error.message)?.[1] ?? error.message
        ).split('=')[0];
        const removed = REMOVED_OPTIONS[`${current.path.join(' ')} ${flag}`];
        throw new CliFailure(
          human_error(
            current,
            `${JSON.stringify(flag)} is not an option for ${command_path(current)}.`,
            2,
            removed !== undefined
              ? { no_route: removed }
              : current.commands.length
                ? { no_route: `Choose a command first. Run ${command_path(current)} --help.` }
                : { recovery: command_shape(current) },
          ),
        );
      }
      // A bare group prints its help.
      if (error instanceof CommanderError && error.code === 'commander.help')
        return { command: current, values: {}, help: help_for(current, current.helpInformation()) };
      throw new CliFailure(human_error(current, stderr || error.message, 2));
    }
    throw error;
  }
  if (!selected) return { command: root, values: {}, help: help_for(root, root.helpInformation()) };
  return selected;
  /** Root help for an agent is its role card. Every other help is Commander's. */
  function help_for(command: CliCommand, commander_help: string): string {
    const role = role_of(environment);
    return command === root && role ? role_card(root, role) : commander_help;
  }
}
/** A removed name prints its stated replacement. Any other unknown name lists the group. */
function unknown_command(group: CliCommand, commander_message: string) {
  const word = group.args[0];
  const removed = removed_command([...group.path, ...group.args]);
  if (removed)
    return human_error(group, `${['panels', removed.path].join(' ')} is no longer a command.`, 2, {
      no_route: removed.lines.join('\n'),
    });
  const suggestion = /\(Did you mean (?:one of )?([^,?]+)[,?]/.exec(commander_message)?.[1];
  const names = group.commands
    .filter((child) => !(child as CliCommand & { _hidden?: boolean })._hidden)
    .map((child) => child.name());
  const lines = [`Commands under ${command_path(group)}: ${names.join(', ')}.`];
  if (suggestion) lines.push(`Did you mean: ${suggestion}?`);
  return human_error(
    group,
    `${JSON.stringify(word)} is not a command under ${command_path(group)}.`,
    2,
    {
      no_route: lines.join('\n'),
    },
  );
}
