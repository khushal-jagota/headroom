/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */

// baseSchemas.ts:17
export type NonNegativeInt = number;

// baseSchemas.ts:15
export type TrimmedNonEmptyString = string;

// baseSchemas.ts:35
export type IsoDateTime = string;

// baseSchemas.ts:112
export type EnvironmentId = string & { readonly __brand: "EnvironmentId" };

/** Schema brand construction at the browser boundary. */
export const EnvironmentId = {
  make(value: string): EnvironmentId {
    if (typeof value !== "string" || value.length === 0) throw new TypeError("Expected a non-empty string");
    return value as EnvironmentId;
  },
};
