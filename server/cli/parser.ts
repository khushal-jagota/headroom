import { CommanderError, InvalidArgumentError } from 'commander';
import type { CommandDefinition } from './contracts.ts';
import { CliArgument, CliCommand, CliOption } from './declarations.ts';
import { create_commands } from './commands.ts';
import { CliFailure, command_path, command_shape, human_error } from './errors.ts';

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
      return { command: current, values: {}, help: stdout };
    if (error instanceof CommanderError || error instanceof InvalidArgumentError) {
      if (error instanceof CommanderError && error.code === 'commander.unknownCommand') {
        const suggested = /\(Did you mean (?:one of )?([^,?]+)[,?]/.exec(error.message)?.[1];
        const child = current.commands.find(
          (command) => command.name() === suggested || command.aliases().includes(suggested ?? ''),
        ) as CliCommand | undefined;
        const recovery =
          current.path.join(' ') === 'manager' && current.args[0] === 'page'
            ? 'panels page --help'
            : child
              ? command_shape(child)
              : undefined;
        throw new CliFailure(
          human_error(
            current,
            `${JSON.stringify(current.args[0])} is not a command under ${command_path(current)}.`,
            2,
            {
              recovery: recovery ?? null,
              ...(recovery
                ? {}
                : {
                    no_route: `No replacement panels command can be inferred for "${current.args[0]}".`,
                  }),
            },
          ),
        );
      }
      if (error instanceof CommanderError && error.code === 'commander.unknownOption') {
        const flag = /^error: unknown option '(.+)'/.exec(error.message)?.[1] ?? error.message;
        throw new CliFailure(
          human_error(
            current,
            `${JSON.stringify(flag)} is not an option for ${command_path(current)}.`,
            2,
            current.commands.length
              ? { no_route: 'No panels call can be named until an action is selected.' }
              : { recovery: command_shape(current) },
          ),
        );
      }
      if (error instanceof CommanderError && error.code === 'commander.help')
        throw new CliFailure(
          human_error(current, `${command_path(current)} needs a command.`, 2, {
            no_route: 'No panels call can be named until an action is selected.',
          }),
        );
      throw new CliFailure(human_error(current, stderr || error.message, 2));
    }
    throw error;
  }
  if (!selected)
    throw new CliFailure(
      human_error(current, 'Select a command. Use --help for available commands.', 2),
    );
  return selected;
}
