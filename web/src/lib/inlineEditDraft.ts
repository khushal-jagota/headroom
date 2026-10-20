/** Logical edit state survives eviction of a closed field's DOM. */
export type InlineEditDraft = {
  value: string | null;
  error: unknown;
  saving: boolean;
};
