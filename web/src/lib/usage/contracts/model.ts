/** Mechanical type projection of T3 75e4ceb9, with `role` from b79d277c. See T3-LICENSE and frontend-translation.md. */
import type { TrimmedNonEmptyString } from "./baseSchemas.ts";

// model.ts:125
export type ModelCapabilities = {
  readonly optionDescriptors?: ReadonlyArray<ProviderOptionDescriptor>;
};

// model.ts:40
export type ProviderOptionDescriptor = SelectProviderOptionDescriptor | BooleanProviderOptionDescriptor;

// model.ts:24
export type SelectProviderOptionDescriptor = {
  readonly id: TrimmedNonEmptyString;
  readonly label: TrimmedNonEmptyString;
  readonly description?: TrimmedNonEmptyString;
  readonly type: "select";
  readonly role?: "reasoning";
  readonly options: ReadonlyArray<ProviderOptionChoice>;
  readonly currentValue?: TrimmedNonEmptyString;
  readonly promptInjectedValues?: ReadonlyArray<TrimmedNonEmptyString>;
};

// model.ts:10
export type ProviderOptionChoice = {
  readonly id: TrimmedNonEmptyString;
  readonly label: TrimmedNonEmptyString;
  readonly description?: TrimmedNonEmptyString;
  readonly isDefault?: boolean;
};

// model.ts:33
export type BooleanProviderOptionDescriptor = {
  readonly id: TrimmedNonEmptyString;
  readonly label: TrimmedNonEmptyString;
  readonly description?: TrimmedNonEmptyString;
  readonly type: "boolean";
  readonly currentValue?: boolean;
};
