/** Existing Python environment identities, manifests, and operator inputs. */
export type EnvironmentKind = 'live' | 'staging';
export type EnvironmentPortPolicy =
  | { readonly kind: 'fixed'; readonly port: number }
  | { readonly kind: 'dynamic'; readonly bind_attempts: number };
export interface EnvironmentDefaults {
  readonly live_port: number;
  readonly staging_bind_attempts: number;
}
export interface EnvironmentCredentialPolicy {
  readonly allowed_keys: ReadonlySet<string>;
  readonly production_only_keys: ReadonlySet<string>;
}
export interface ResolvedEnvironmentInstance {
  readonly kind: EnvironmentKind;
  readonly instance_id: string;
  readonly environment_root: string;
  readonly instance_root: string;
  readonly db_path: string;
  readonly managed_files_root: string;
  readonly logs_dir: string;
  readonly dispatcher_lock_path: string;
  readonly server_control_socket_path: string;
  readonly port_policy: EnvironmentPortPolicy;
  readonly credentials_env_file: string | null;
  readonly allowed_repository_roots: readonly string[];
  readonly fixture_version: string | null;
  readonly prepared: boolean;
  readonly running: boolean;
}
export interface EnvironmentManifest extends Omit<
  ResolvedEnvironmentInstance,
  'allowed_repository_roots' | 'prepared' | 'running'
> {
  readonly prepared_at: number | null;
  readonly repository_roots: readonly string[];
}
export interface EnvironmentManifestPayload extends Omit<EnvironmentManifest, 'port_policy'> {
  readonly runtime_port: EnvironmentPortPolicy;
}
export type EnvironmentInspection = Omit<EnvironmentManifest, 'port_policy'> & {
  readonly prepared: boolean;
} & (
    | { readonly runtime_port_policy: 'fixed'; readonly port: number; readonly running: boolean }
    | { readonly runtime_port_policy: 'dynamic'; readonly bind_attempts: number }
  );
export interface EnvironmentRequest {
  readonly kind: EnvironmentKind;
  readonly environment_root: string;
  readonly repository_roots: readonly string[];
  readonly instance_id?: string | null;
  readonly port?: number | null;
  readonly credentials_env_file?: string | null;
}
export interface PrepareEnvironmentRequest extends EnvironmentRequest {
  readonly defaults?: EnvironmentDefaults;
  readonly now?: number;
}
export interface ResetEnvironmentRequest extends EnvironmentRequest {
  readonly now?: number;
}
export interface ResolveEnvironmentRequest extends Omit<EnvironmentRequest, 'repository_roots'> {
  readonly allowed_repository_roots?: readonly string[];
  readonly requested_repository_roots?: readonly string[];
  readonly defaults?: EnvironmentDefaults;
  readonly fixture_version?: string | null;
  readonly prepared?: boolean;
  readonly running?: boolean;
}
/** Explicit adapters preserve fixture/swap failure coverage without process-wide test flags. */
export interface MaterializationOperations {
  build_fixture(database: string, now: number): void | Promise<void>;
  rename(source: string, destination: string): Promise<void>;
}
export interface EnvironmentRunOptions {
  readonly credentials: Readonly<Record<string, string>>;
  readonly ambient: Readonly<Record<string, string | undefined>>;
  readonly hermes_python: string;
  readonly runtime_port: number;
}
export class EnvironmentValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EnvironmentValidationError';
  }
}
