/** A recording environment has no subprocess or network route for backend actions. */
import type { BackendProbeEnvironment, CommandOutcome } from './contracts.ts';
export class RecordingBackendProbeEnvironment implements BackendProbeEnvironment {
  readonly commands: string[][] = [];
  readonly registry_lookups: string[] = [];
  readonly executables = new Map<string, string>();
  readonly configured_executables = new Map<string, string>();
  readonly real_paths = new Map<string, string>();
  readonly writable_prefixes = new Set<string>();
  readonly outcomes = new Map<string, CommandOutcome>();
  readonly registry_versions = new Map<string, string>();
  executable_path(name: string): string | null {
    return this.executables.get(name) ?? null;
  }
  configured_executable_path(name: string): string | null {
    return this.configured_executables.get(name) ?? null;
  }
  real_path(path: string): string {
    return this.real_paths.get(path) ?? path;
  }
  user_local_npm_prefix(): string {
    return '/synthetic/.local';
  }
  prefix_is_owned_and_writable(prefix: string): boolean {
    return this.writable_prefixes.has(prefix);
  }
  async run(argv: readonly string[]): Promise<CommandOutcome> {
    this.commands.push([...argv]);
    return (
      this.outcomes.get(JSON.stringify(argv)) ?? {
        exit_code: -1,
        standard_output: '',
        standard_error: 'no such command',
      }
    );
  }
  async latest_released_version(package_name: string): Promise<string | null> {
    this.registry_lookups.push(package_name);
    return this.registry_versions.get(package_name) ?? null;
  }
}
