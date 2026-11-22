import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
import type { ROLE_SKILL_NAMES } from '../../skills.ts';
export interface SkillInstallation {
  snapshot: CanonicalSkillSnapshot;
  root: string;
  native_home: string;
  /** Present only on machine code that owns the colocated server account folders. */
  legacy_server_skill_parent?: string;
  /** One path component inside native_home: the provider's skills folder. */
  native_skills_directory: string;
  /** Whose child the snapshot is for. It names the role skill the snapshot must require. */
  owner_kind: keyof typeof ROLE_SKILL_NAMES;
}
export type SkillRequest = { id: number; install: SkillInstallation } | { stop: true };
export type SkillReply =
  | { id: number; generation: string }
  | { id: number; error: { name: string; message: string; code?: string }; fatal?: boolean }
  | { cleanup_error: { name: string; message: string } };
export const SKILL_LOCK_RETRY_MS = 50;
