/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */
import type { IsoDateTime, ModelCapabilities, ProviderDriverKind, ProviderInstanceId, ServerProviderUsageLimits, TrimmedNonEmptyString } from "./index";

// server.ts:188
export type ServerProvider = {
  readonly instanceId: ProviderInstanceId;
  readonly driver: ProviderDriverKind;
  readonly displayName?: TrimmedNonEmptyString;
  readonly accentColor?: TrimmedNonEmptyString;
  readonly badgeLabel?: TrimmedNonEmptyString;
  readonly continuation?: ServerProviderContinuation;
  readonly showInteractionModeToggle?: boolean;
  readonly reportsContextWindow?: boolean;
  readonly requiresNewThreadForModelChange?: boolean;
  readonly supportsConversationRollback?: boolean;
  readonly supportsTextGeneration?: boolean;
  readonly setup?: {
  readonly canAuthenticate: boolean;
  readonly canInstall: boolean;
};
  readonly enabled: boolean;
  readonly installed: boolean;
  readonly version: (TrimmedNonEmptyString) | null;
  readonly status: ServerProviderState;
  readonly auth: ServerProviderAuth;
  readonly checkedAt: IsoDateTime;
  readonly message?: TrimmedNonEmptyString;
  readonly availability?: ServerProviderAvailability;
  readonly unavailableReason?: TrimmedNonEmptyString;
  readonly models: ReadonlyArray<ServerProviderModel>;
  readonly slashCommands: ReadonlyArray<ServerProviderSlashCommand>;
  readonly skills: ReadonlyArray<ServerProviderSkill>;
  readonly workspaceSnapshots?: ReadonlyArray<ServerProviderWorkspaceSnapshot>;
  readonly usageLimits?: ServerProviderUsageLimits;
  readonly versionAdvisory?: ServerProviderVersionAdvisory;
  readonly updateState?: ServerProviderUpdateState;
};

// server.ts:146
export type ServerProviderContinuation = {
  readonly groupKey: TrimmedNonEmptyString;
};

// server.ts:51
export type ServerProviderState = "ready" | "warning" | "error" | "disabled";

// server.ts:61
export type ServerProviderAuth = {
  readonly status: ServerProviderAuthStatus;
  readonly type?: TrimmedNonEmptyString;
  readonly label?: TrimmedNonEmptyString;
  readonly email?: TrimmedNonEmptyString;
};

// server.ts:54
export type ServerProviderAuthStatus = "authenticated" | "unauthenticated" | "unknown";

// server.ts:143
export type ServerProviderAvailability = "available" | "unavailable";

// server.ts:69
export type ServerProviderModel = {
  readonly slug: TrimmedNonEmptyString;
  readonly name: TrimmedNonEmptyString;
  readonly shortName?: TrimmedNonEmptyString;
  readonly subProvider?: TrimmedNonEmptyString;
  readonly aliases?: ReadonlyArray<TrimmedNonEmptyString>;
  readonly badge?: "new";
  readonly isCustom: boolean;
  readonly isDefault?: boolean;
  readonly isLegacy?: boolean;
  readonly capabilities: (ModelCapabilities) | null;
};

// server.ts:88
export type ServerProviderSlashCommand = {
  readonly name: TrimmedNonEmptyString;
  readonly description?: TrimmedNonEmptyString;
  readonly input?: ServerProviderSlashCommandInput;
};

// server.ts:83
export type ServerProviderSlashCommandInput = {
  readonly hint: TrimmedNonEmptyString;
};

// server.ts:95
export type ServerProviderSkill = {
  readonly name: TrimmedNonEmptyString;
  readonly description?: TrimmedNonEmptyString;
  readonly path: TrimmedNonEmptyString;
  readonly scope?: TrimmedNonEmptyString;
  readonly enabled: boolean;
  readonly displayName?: TrimmedNonEmptyString;
  readonly shortDescription?: TrimmedNonEmptyString;
  readonly userInvocationOnly?: boolean;
  readonly userInvocable?: boolean;
};

// server.ts:118
export type ServerProviderWorkspaceSnapshot = {
  readonly cwd: TrimmedNonEmptyString;
  readonly checkedAt: IsoDateTime;
  readonly slashCommands: ReadonlyArray<ServerProviderSlashCommand>;
  readonly skills: ReadonlyArray<ServerProviderSkill>;
};

// server.ts:158
export type ServerProviderVersionAdvisory = {
  readonly status: ServerProviderVersionAdvisoryStatus;
  readonly currentVersion: (TrimmedNonEmptyString) | null;
  readonly latestVersion: (TrimmedNonEmptyString) | null;
  readonly updateCommand: (TrimmedNonEmptyString) | null;
  readonly canUpdate: boolean;
  readonly checkedAt: (IsoDateTime) | null;
  readonly message: (TrimmedNonEmptyString) | null;
};

// server.ts:151
export type ServerProviderVersionAdvisoryStatus = "unknown" | "current" | "behind_latest";

// server.ts:179
export type ServerProviderUpdateState = {
  readonly status: ServerProviderUpdateStatus;
  readonly startedAt: (IsoDateTime) | null;
  readonly finishedAt: (IsoDateTime) | null;
  readonly message: (TrimmedNonEmptyString) | null;
  readonly output: (string) | null;
};

// server.ts:169
export type ServerProviderUpdateStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "unchanged";

// Copied unchanged from server.ts:254–255.
export const isProviderAvailable = (snapshot: ServerProvider): boolean =>
  snapshot.availability !== "unavailable";
