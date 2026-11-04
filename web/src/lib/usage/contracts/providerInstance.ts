/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */

// providerInstance.ts:82
export type ProviderInstanceId = string & { readonly __brand: "ProviderInstanceId" };

// providerInstance.ts:70
export type ProviderDriverKind = string & { readonly __brand: "ProviderDriverKind" };

function makeProviderSlug(value: string): string {
  if (typeof value !== "string" || value.length > 64 || !/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(value)) {
    throw new TypeError("Expected a provider slug");
  }
  return value;
}
export const ProviderDriverKind = { make: (value: string): ProviderDriverKind => makeProviderSlug(value) as ProviderDriverKind };
export const ProviderInstanceId = { make: (value: string): ProviderInstanceId => makeProviderSlug(value) as ProviderInstanceId };
