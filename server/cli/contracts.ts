/** CLI declarations contain no server runtime imports. */
export interface Parameter {
  name: string;
  opts?: string[];
  secondary_opts?: string[];
  argument?: boolean;
  nargs?: number;
  type: 'string' | 'integer' | 'float' | 'boolean' | 'choice' | 'path';
  choices?: string[];
  min?: number;
  max?: number;
  required?: boolean;
  multiple?: boolean;
  flag?: boolean;
  default?: unknown;
  help?: string;
  hidden?: boolean;
  show_default?: boolean | string;
  metavar?: string;
  exists?: boolean;
  file_okay?: boolean;
  dir_okay?: boolean;
}
export interface CommandDefinition {
  path: string[];
  help: string;
  hidden?: boolean;
  group?: boolean;
  parameters: Parameter[];
}
export interface CommandResult {
  exit_code: number;
  stdout: string;
  stderr: string;
}
export interface Invocation {
  command: CommandDefinition;
  values: Record<string, any>;
  environment: NodeJS.ProcessEnv;
  stdin: () => Promise<Buffer>;
}
export type CommandHandler = (invocation: Invocation) => Promise<CommandResult>;
