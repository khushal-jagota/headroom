/** Match the supported locale used by navigation queries and Ticket comparisons. */
export function workItemLocale(): string {
  return typeof navigator !== "undefined" && navigator.language.startsWith("de")
    ? "de-DE"
    : "en-US";
}
