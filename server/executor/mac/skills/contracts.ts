import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
export interface SkillInstallation {
  snapshot: CanonicalSkillSnapshot;
  root: string;
  native_home: string;
}
export type SkillRequest = { id: number; install: SkillInstallation } | { stop: true };
export type SkillReply =
  | { id: number; generation: string }
  | { id: number; error: { name: string; message: string; code?: string }; fatal?: boolean }
  | { cleanup_error: { name: string; message: string } };
export const SKILL_LOCK_RETRY_MS = 50;
