/** The executable binds the operator table; the production factory owns its adapters. */
import { operator_commands } from '../environments/commands.ts';
import { create_production_operator_dependencies } from '../environments/operator_dependencies.ts';
import { OperatorInterrupted } from '../environments/command_contracts.ts';
import type { OperatorArguments, OperatorName } from '../environments/command_contracts.ts';
import type { CommandResult, Invocation } from './contracts.ts';
import { command_shape, human_error } from './errors.ts';

async function invoke<Name extends OperatorName>(
  name: Name,
  invocation: Invocation,
): Promise<CommandResult> {
  const values = { ...invocation.values };
  for (const alias of ['as_json', 'json_output']) {
    if (!Object.hasOwn(values, alias)) continue;
    values.json = values[alias];
    delete values[alias];
  }
  if (name === 'watchdog') {
    values.db = values.db_path;
    delete values.db_path;
  }
  const dependencies = await create_production_operator_dependencies(
    name,
    undefined,
    invocation.environment,
  );
  const result = await operator_commands[name].handler(
    values as OperatorArguments[Name],
    dependencies,
  );
  if (!('failure_message' in result)) return result;
  const failure = human_error(
    invocation.command,
    result.failure_message,
    result.exit_code,
    name === 'serve' || name === 'restart'
      ? { no_route: 'No follow-up panels call is available for this process failure.' }
      : { recovery: command_shape(invocation.command) },
  );
  return { ...failure, stderr: result.stderr_prefix + failure.stderr };
}

export async function run_operator_command(invocation: Invocation): Promise<CommandResult> {
  const selected = Object.entries(operator_commands).find(
    ([, entry]) => entry.command === invocation.command,
  );
  if (!selected) throw new Error('Operator command is absent from its canonical table');
  try {
    return await invoke(selected[0] as OperatorName, invocation);
  } catch (error) {
    if (!(error instanceof OperatorInterrupted) || error.signal !== 'SIGINT') throw error;
    const failure = human_error(invocation.command, error.message, 1, {
      no_route: 'No follow-up panels call is required.',
    });
    return { ...failure, stderr: error.stderr_prefix + failure.stderr };
  }
}
