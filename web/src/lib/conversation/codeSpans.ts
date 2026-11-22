/** Plain words that carry code: a plan step or a short status written without Markdown
 *  ("Confirm the race with -count=50") still names flags, calls and files. Those tokens are
 *  set in the inline-code face, as Markdown's `code` is, so code looks like code everywhere
 *  in the conversation. Backticked spans are code by the writer's own word. */
export type TextSpan = { text: string; code: boolean };

const FLAG = /^--?[A-Za-z][\w-]*(=\S+)?$/;
const CALL = /^[A-Za-z_][\w.]*\(\)$/;
const FILE = /^[\w.@/-]*[\w-]\.(go|ts|tsx|js|jsx|mjs|py|rb|rs|swift|kt|java|c|h|cc|cpp|json|toml|yaml|yml|md|txt|csv|sql|svelte|css|html|sh|ipa|lock)(:\d+)?$/;
const PATH = /^\.{0,2}\/[\w.@/-]+$/;

export function looksLikeCode(token: string): boolean {
  return FLAG.test(token) || CALL.test(token) || FILE.test(token) || PATH.test(token);
}

export function codeSpans(text: string): TextSpan[] {
  const spans: TextSpan[] = [];
  const push = (part: string, code: boolean): void => {
    if (part === "") return;
    const last = spans[spans.length - 1];
    if (last !== undefined && last.code === code && !code) last.text += part;
    else spans.push({ text: part, code });
  };
  // Backticks first: what the writer marked is code, whatever it looks like.
  const pieces = text.split(/(`[^`]+`)/);
  for (const piece of pieces) {
    if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
      push(piece.slice(1, -1), true);
      continue;
    }
    for (const part of piece.split(/(\s+)/)) {
      if (part === "" || /^\s+$/.test(part)) { push(part, false); continue; }
      // Punctuation that ends a sentence stays prose, so it never rides inside the code face.
      if (looksLikeCode(part)) { push(part, true); continue; }
      const match = /^(.*?)([.,;:!?)]*)$/.exec(part)!;
      let [, core, trailing] = match;
      if (trailing.startsWith(")") && core.endsWith("(")) { core += ")"; trailing = trailing.slice(1); }
      if (core !== "" && looksLikeCode(core)) {
        push(core, true);
        push(trailing, false);
      } else push(part, false);
    }
  }
  return spans;
}
