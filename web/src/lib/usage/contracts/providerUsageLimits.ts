/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */
import type { IsoDateTime, NonNegativeInt, ProviderDriverKind, ProviderInstanceId, TrimmedNonEmptyString, UsageLimitSourceId } from "./index";

// providerUsageLimits.ts:50
export type ServerProviderUsageLimits = {
  readonly checkedAt: IsoDateTime;
  readonly windows: ReadonlyArray<ServerProviderUsageWindow>;
  readonly resetCredits?: ServerProviderResetCredits;
  readonly unavailable?: {
  readonly reason: "unsupported" | "probeFailed";
  readonly message?: TrimmedNonEmptyString;
};
};

// providerUsageLimits.ts:20
export type ServerProviderUsageWindow = {
  readonly id: TrimmedNonEmptyString;
  readonly kind: "session" | "weekly" | "monthly" | "other";
  readonly label: TrimmedNonEmptyString;
  readonly usedPercent: number;
  readonly resetsAt?: IsoDateTime;
  readonly windowDurationMins?: NonNegativeInt;
};

// providerUsageLimits.ts:35
export type ServerProviderResetCredits = {
  readonly availableCount: NonNegativeInt;
  readonly nextExpiresAt?: IsoDateTime;
  readonly nextCreditId?: TrimmedNonEmptyString;
};

// providerUsageLimits.ts:69
export type ProviderUsageLimitsUpdate = {
  readonly windows: ReadonlyArray<ServerProviderUsageWindow>;
};

// providerUsageLimits.ts:79
export type UsageLimitSourceAccount = {
  readonly id: TrimmedNonEmptyString;
  readonly driver: ProviderDriverKind;
  readonly email?: TrimmedNonEmptyString;
  readonly plan?: TrimmedNonEmptyString;
  readonly usageLimits: ServerProviderUsageLimits;
};

// providerUsageLimits.ts:95
export type UsageLimitSourceSnapshot = {
  readonly id: UsageLimitSourceId;
  readonly kind: "cliproxy";
  readonly label: TrimmedNonEmptyString;
  readonly checkedAt: IsoDateTime;
  readonly accounts: ReadonlyArray<UsageLimitSourceAccount>;
  readonly error?: TrimmedNonEmptyString;
};

// providerUsageLimits.ts:105
export type UsageLimitSourceSnapshots = ReadonlyArray<UsageLimitSourceSnapshot>;

// providerUsageLimits.ts:108
export type UsageLimitSourceConsumeResetCreditInput = {
  readonly sourceId: UsageLimitSourceId;
  readonly accountId: TrimmedNonEmptyString;
  readonly creditId: TrimmedNonEmptyString;
};

// providerUsageLimits.ts:116
export type ProviderConsumeResetCreditInput = {
  readonly instanceId: ProviderInstanceId;
} | UsageLimitSourceConsumeResetCreditInput;

// providerUsageLimits.ts:132
export type ProviderConsumeResetCreditOutcome = "reset" | "nothingToReset" | "noCredit" | "alreadyRedeemed";

// providerUsageLimits.ts:140
export type ProviderConsumeResetCreditResult = {
  readonly outcome: ProviderConsumeResetCreditOutcome;
  readonly warning?: TrimmedNonEmptyString;
};

// providerUsageLimits.ts:148
export type UsageLimitsReport = {
  readonly createdAt: IsoDateTime;
  readonly accounts: ReadonlyArray<{
  readonly id: TrimmedNonEmptyString;
  readonly driver: ProviderDriverKind;
  readonly label: TrimmedNonEmptyString;
  readonly plan?: TrimmedNonEmptyString;
  readonly email?: TrimmedNonEmptyString;
  readonly sourceLabel?: TrimmedNonEmptyString;
  readonly instanceId?: ProviderInstanceId;
  readonly resetCreditInput?: ProviderConsumeResetCreditInput;
  readonly displayName?: string;
  readonly accentColor?: string;
  readonly limits: ServerProviderUsageLimits;
}>;
  readonly notices: ReadonlyArray<string>;
};
