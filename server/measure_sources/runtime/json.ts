/** Scripts retain whole integers that exceed JavaScript's exact number range. */
export function parse_runtime_json(text: string): unknown {
  return JSON.parse(text, (_key, value: unknown, context?: { source?: string }) =>
    typeof value === 'number' && !Number.isSafeInteger(value) && !/[.eE]/.test(context!.source!)
      ? BigInt(context!.source!)
      : value,
  );
}
export function stringify_runtime_json(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (typeof item === 'number' && !Number.isFinite(item))
      throw new RangeError('Source JSON numbers must be finite');
    if (typeof item === 'number' && Number.isInteger(item) && !Number.isSafeInteger(item))
      return (JSON as unknown as { rawJSON(text: string): unknown }).rawJSON(item.toExponential());
    return typeof item === 'bigint'
      ? (JSON as unknown as { rawJSON(text: string): unknown }).rawJSON(String(item))
      : item;
  });
}
