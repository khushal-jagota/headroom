import type { CommandDefinition, CommandResult } from './contracts.ts';

export class MissingResponseValue extends Error {
  constructor() {
    super('response is missing a required value');
  }
}

export class CliFailure extends Error {
  readonly result: CommandResult;
  constructor(result: CommandResult) {
    super(result.stderr);
    this.result = result;
  }
}
const recovery: Record<string, string> = {
  'send-message': 'panels send-message --owner --message TEXT',
  'worker-type save': 'panels worker-type save < RECORD_FILE',
  'worker-type skill': 'panels worker-type skill WORKER_TYPE --description TEXT < SKILL_FILE',
  'skill edit': 'panels skill edit NAME --description TEXT < SKILL_FILE',
  'project set': 'panels project set PROJECT_ID FIELD --value VALUE',
  'ticket delete': 'panels ticket delete TICKET_ID',
  'ticket create': 'panels ticket create --input-json FILE',
  'ticket assign': 'panels ticket assign TICKET_ID --chief',
  'manager settle': 'panels manager settle ITEM_ID',
  'ticket edit': 'panels ticket edit TICKET_ID --input-json FILE',
  'ticket proposal': 'panels ticket proposal TICKET_ID ACTION',
  'ticket request-help': 'panels ticket request-help TICKET_ID < MESSAGE_FILE',
  'ticket complete': 'panels ticket complete TICKET_ID FIELD --value VALUE',
  'manager create': 'panels manager create --title TEXT --project-id PROJECT_ID',
  'manager delete': 'panels manager delete ITEM_ID --yes',
  'page edit': 'panels page schema',
  'page show': 'panels page show --manager ID',
  'manager set': 'panels manager set ITEM_ID FIELD --value VALUE',
  'manager conversation send': 'panels manager conversation send ITEM_ID --message TEXT',
  'worker propose': 'panels worker propose TICKET_ID < PROPOSAL_FILE',
  'worker request-help': 'panels worker request-help TICKET_ID < MESSAGE_FILE',
  'worker recap': 'panels worker recap TICKET_ID < RECAP_FILE',
};
export function command_path(command: CommandDefinition): string {
  return ['panels', ...command.path].join(' ');
}
export function command_shape(command: CommandDefinition): string {
  const override = recovery[command.path.join(' ')];
  if (override) return override;
  const options = command.parameters.filter((p) => !p.argument && !p.hidden);
  return [
    command_path(command),
    ...options
      .filter((p) => p.required)
      .map(
        (p) =>
          (p.opts?.find((o) => o.startsWith('--')) ?? p.opts![0]) + (p.flag ? '' : ' ' + p.metavar),
      ),
    ...(options.some((p) => !p.required) ? ['[OPTIONS]'] : []),
    ...command.parameters.filter((p) => p.argument).map((p) => p.metavar),
  ].join(' ');
}
export function human_error(
  command: CommandDefinition,
  message: string,
  exit_code = 1,
  options: { recovery?: string | null; no_route?: string } = {},
): CommandResult {
  const line = message
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
