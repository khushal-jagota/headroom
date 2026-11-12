/** Provider units implement these Python-shaped local service boundaries. */
import type { CodexLoginStanding, CommandOutcome } from './contracts.ts';
export interface CodexModel {
  readonly model_id: string;
  readonly display_name: string;
  readonly reasoning_effort_options: readonly string[];
  readonly default_reasoning_effort: string;
  readonly is_default: boolean;
}
export interface CodexModelCatalog {
  readonly models: readonly CodexModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
  readonly default_reasoning_effort: string | null;
  readonly complete: boolean;
}
export interface CodexModelCatalogSource {
  (codex_executable: string): Promise<CodexModelCatalog>;
}
export interface ClaudeModel {
  readonly model_id: string;
  readonly display_name: string;
  readonly resolved_model_id: string;
  readonly reasoning_effort_options: readonly string[];
}
export interface ClaudeModelCatalog {
  readonly models: readonly ClaudeModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
}
export interface ClaudeModelCatalogSource {
  (claude_executable: string): Promise<ClaudeModelCatalog>;
}
/** Hermes' retained Python inventory script reports its own JSON through this door. */
export interface HermesModelCatalogSource {
  (options: { refresh: boolean }): Promise<CommandOutcome>;
}
export interface CodexLoginGeneration {
  readonly account_id: string;
  readonly last_refresh: string;
}
export interface StoredCodexLogin {
  readonly present: boolean;
  readonly readable: boolean;
  readonly account_label: string | null;
  readonly access_token_expires_at: Date | null;
  readonly generation: CodexLoginGeneration | null;
  access_token_has_expired(options: { now: Date }): boolean;
}
export interface CodexLoginRejectionMemory {
  remember(generation: CodexLoginGeneration | null): void;
  forget(generation: CodexLoginGeneration | null): void;
  applies_to(generation: CodexLoginGeneration | null): boolean;
}
export interface SavedCodexLoginSource {
  read_stored_codex_login(home?: string | null): StoredCodexLogin;
  read_codex_login_standing(
    remembered_rejection: CodexLoginRejectionMemory | null,
  ): CodexLoginStanding;
}
export type SignInState = 'pending' | 'confirmed' | 'unconfirmed' | 'ended';
export interface CodexSignIn {
  readonly session_id: string;
  readonly state: SignInState;
  readonly verification_url: string | null;
  readonly user_code: string | null;
  readonly started_at: Date;
  readonly deadline_at: Date;
  readonly detail: string | null;
  readonly account_label: string | null;
}
export interface CodexSignInService {
  read(): CodexSignIn | null;
  start(): Promise<CodexSignIn>;
  cancel(session_id: string): Promise<CodexSignIn>;
  shutdown(): Promise<void>;
}
export class CodexSignInUnavailable extends Error {}
export class UnknownSignInSession extends Error {}
export class CodexModelCatalogUnavailable extends Error {}
export class ClaudeModelCatalogUnavailable extends Error {}
