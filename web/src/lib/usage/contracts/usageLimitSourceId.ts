/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */

// usageLimitSourceId.ts:8
export type UsageLimitSourceId = string & { readonly __brand: "UsageLimitSourceId" };

export const UsageLimitSourceId = {
  make(value: string): UsageLimitSourceId {
    if (typeof value !== "string") throw new TypeError("Expected a string");
    return value as UsageLimitSourceId;
  },
};
