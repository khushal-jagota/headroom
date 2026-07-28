/** A backend detail as something a person can read.
 *
 * Structured payloads are laid out rather than shown as minified JSON. Everything else
 * is already prose and is left exactly as it was written.
 */
export function readableConversationDetail(
  detail: string | null | undefined
): string | null {
  if (detail === null || detail === undefined) return null;
  const trimmed = detail.trim();
  if (trimmed === "") return null;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return detail;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return detail;
  }
}
