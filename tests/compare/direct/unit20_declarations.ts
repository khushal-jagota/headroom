/** Test-only projection of native declarations onto the migration contract. */
import { create_commands } from "../../../server/cli/commands.ts";
import {
  CliArgument,
  CliOption,
  type CliCommand,
} from "../../../server/cli/declarations.ts";

function parameter(input: CliArgument | CliOption): Record<string, unknown> {
  const argument = input instanceof CliArgument;
  const parser = input.convert;
  const value: Record<string, unknown> = {
    name: input.key,
    type: parser.kind,
    required: argument ? input.required : input.mandatory,
    nargs: argument && input.variadic ? -1 : 1,
    default: input.defaultValue ?? null,
  };
  if (argument) {
    value.argument = true;
    if (input.envNames.length)
      value.envvar =
        input.envNames.length === 1 ? input.envNames[0] : input.envNames;
  } else {
    value.opts = input.flags
      .split(/[ ,|]+/)
      .filter((flag) => flag.startsWith("-"));
    value.secondary_opts = [];
    value.multiple = input.repeat;
    value.flag = !input.required && !input.optional;
    value.hidden = input.hidden;
  }
  if (parser.choices) value.choices = parser.choices;
  if (parser.min !== undefined) value.min = parser.min;
  if (parser.max !== undefined) value.max = parser.max;
  if (parser.kind === "path") {
    Object.assign(value, {
      exists: parser.mustExist ?? false,
      file_okay: parser.pathKind !== "directory",
      dir_okay: parser.pathKind !== "file",
      readable: true,
      writable: false,
      executable: false,
      resolve_path: false,
      allow_dash: false,
    });
  }
  return value;
}

const records: Record<string, unknown>[] = [];
function visit(command: CliCommand): void {
  const inputs: Record<string, unknown>[] = [];
  const byKey = new Map<string, Record<string, unknown>>();
  for (const input of command.inputs) {
    if (input instanceof CliOption && input.inverse) {
      const positive = byKey.get(input.key);
      if (!positive)
        throw new Error(`Inverse option lacks a positive option: ${input.key}`);
      (positive.secondary_opts as string[]).push(
        ...input.flags.split(/[ ,|]+/).filter((flag) => flag.startsWith("-")),
      );
      continue;
    }
    const value = parameter(input);
    byKey.set(input.key, value);
    inputs.push(value);
  }
  records.push({
    path: command.path,
    hidden: (command as unknown as { _hidden: boolean })._hidden ?? false,
    group: command.commands.length > 0,
    parameters: inputs,
  });
  for (const child of [...command.commands].sort((a, b) =>
    a.name() < b.name() ? -1 : a.name() > b.name() ? 1 : 0,
  ))
    visit(child as CliCommand);
}
visit(create_commands({}));
process.stdout.write(JSON.stringify(records));
