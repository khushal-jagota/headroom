import type { CommandDefinition, CommandResult } from './contracts.ts';
import { CliArgument, CliOption } from './declarations.ts';

export class MissingResponseValue extends Error {
  constructor() {
    super('response is missing a required value');
  }
}

export class CliFailure extends Error {
  readonly result: CommandResult;
  constructor(result: CommandResult, options?: { cause?: unknown }) {
    super(result.stderr, options);
    this.result = result;
  }
}
/** Command shapes that the generated shape cannot say, such as text on stdin. */
const recovery: Record<string, string> = {
  'send-message':
    'panels send-message --owner|--chief|--ticket TICKET_ID|--manager MANAGER_ID < MESSAGE_FILE',
  recap: 'panels recap < RECAP_FILE',
  'ticket propose': 'panels ticket propose < PROPOSAL_FILE',
  'ticket approve': 'panels ticket approve TICKET_ID --ceiling STAGE',
  'ticket send-back': 'panels ticket send-back TICKET_ID < GUIDANCE_FILE',
  'ticket complete': 'panels ticket complete TICKET_ID FIELD < VALUE_FILE',
  'ticket create': 'panels ticket create < TICKET_FILE',
  'ticket edit': 'panels ticket edit TICKET_ID < EDIT_FILE',
  'ticket assign': 'panels ticket assign TICKET_ID --manager MANAGER_ID|--chief',
  'ticket show': 'panels ticket show [TICKET_ID] [PART_NAMES...]',
  'manager create': 'panels manager create --title TEXT --project-id PROJECT_ID',
  'manager delete': 'panels manager delete MANAGER_ID --yes',
  'manager set': 'panels manager set MANAGER_ID FIELD < VALUE_FILE',
  'manager show': 'panels manager show [MANAGER_ID] [PART_NAMES...]',
  'project set': 'panels project set PROJECT_ID FIELD < VALUE_FILE',
  'page edit': 'panels page schema',
  'page show': 'panels page show --manager MANAGER_ID|--chief',
  'skill edit': 'panels skill edit NAME --description TEXT < SKILL_FILE',
  'worker-type save': 'panels worker-type save < RECORD_FILE',
  'worker-type skill': 'panels worker-type skill WORKER_TYPE --description TEXT < SKILL_FILE',
};
export function command_path(command: CommandDefinition): string {
  return ['panels', ...command.path].join(' ');
}
export function command_shape(command: CommandDefinition): string {
  const override = recovery[command.path.join(' ')];
  if (override) return override;
  const options = command.inputs.filter(
    (input): input is CliOption => input instanceof CliOption && !input.hidden,
  );
  const parts = [command_path(command)];
  for (const input of options.filter((option) => option.mandatory)) {
    const value = /<([^>]+)>|\[([^\]]+)\]/.exec(input.flags);
    parts.push([input.long ?? input.short, ...(value ? [value[1] ?? value[2]] : [])].join(' '));
  }
  if (options.some((option) => !option.mandatory)) parts.push('[OPTIONS]');
  for (const input of command.inputs) {
    if (input instanceof CliArgument)
      parts.push(
        (input.required ? input.name() : `[${input.name()}]`) + (input.variadic ? '...' : ''),
      );
  }
  return parts.join(' ');
}
export function human_error(
  command: CommandDefinition,
  message: string,
  exit_code = 1,
  options: { recovery?: string | null; no_route?: string } = {},
): CommandResult {
  const line = message
    .replace(/^error:\s*/, '')
    .split(/\r?\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .join(' ');
  let stderr = 'error: ' + line + '\n';
  if (options.no_route !== undefined) stderr += options.no_route + '\n';
  else {
    const shape =
      options.recovery === null
        ? undefined
        : (options.recovery ??
          (/(?:^|[`'" ])panels(?:[ `]|$)/.test(line) ? undefined : command_shape(command)));
    if (shape !== undefined) stderr += 'Use: ' + shape + '\n';
  }
  return { exit_code, stdout: '', stderr };
}
export function usage_error(
  command: CommandDefinition,
  message: string,
  options: { recovery?: string | null; no_route?: string } = {},
): never {
  throw new CliFailure(human_error(command, message, 2, options));
}
