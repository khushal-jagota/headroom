import type { CliCommand } from './declarations.ts';

export type CommandDefinition = CliCommand;
export interface CommandResult {
  launch?: import('../environments/command_contracts.ts').PreparedOperatorLaunch;
  exit_code: number;
  stdout: string;
  stderr: string;
}
export interface Invocation {
  command: CommandDefinition;
  values: Record<string, any>;
  environment: NodeJS.ProcessEnv;
  stdin: () => Promise<Buffer>;
  /** Flush incremental stderr before the operation resumes. */
  write_stderr?: (text: string) => Promise<void>;
}
export type CommandHandler = (invocation: Invocation) => Promise<CommandResult>;
