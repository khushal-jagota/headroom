export type ComposerCatalogToken = {
  start: 0;
  end: number;
  trigger: "/" | "$" | "@";
  typedSoFar: string | null;
};

/** The message-start catalog token under the cursor.
 *
 * A composer shortcut is message syntax, not line syntax. Only a trigger at absolute
 * offset zero starts one. This prevents ordinary prose, pasted text, and later lines from
 * unexpectedly taking over the composer with a catalog menu.
 */
export function catalogTokenAtMessageStart(
  written: string,
  at: number
): ComposerCatalogToken | null {
  const trigger = written[0];
  if (trigger !== "/" && trigger !== "$" && trigger !== "@") return null;
  let end = 1;
  while (end < written.length && !/\s/.test(written[end] ?? "")) end += 1;
  return {
    start: 0,
    end,
    trigger,
    typedSoFar: at > 0 && at <= end ? written.slice(1, at) : null
  };
}
