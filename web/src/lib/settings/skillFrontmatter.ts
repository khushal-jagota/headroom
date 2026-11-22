/** A managed skill's Markdown body, split into its leading frontmatter and the instructions.
 *
 * The server keeps a skill's own frontmatter apart from `markdown_body`, but some stored
 * bodies still begin with a copy of it, sometimes two: a `---` line, YAML keys, a closing
 * `---` line. Rendered as Markdown, that copy reads as a broken heading. Settings shows and
 * edits `body` only, and writes `frontmatter + body` back, so a save changes nothing the
 * person did not see.
 *
 * `frontmatter` is every leading block, with the blank lines around them, so that
 * `frontmatter + body` is always the original string. A block counts only when it closes
 * and its first line is a `key:`; a body that opens with a Markdown rule is left alone.
 */
export function splitSkillFrontmatter(markdown: string): { frontmatter: string; body: string } {
  let offset = skipBlankLines(markdown, 0);
  for (let next = blockEnd(markdown, offset); next !== null; next = blockEnd(markdown, offset)) {
    offset = skipBlankLines(markdown, next);
  }
  return { frontmatter: markdown.slice(0, offset), body: markdown.slice(offset) };
}

function lineAt(text: string, start: number): { line: string; next: number } {
  const newline = text.indexOf("\n", start);
  const stop = newline < 0 ? text.length : newline;
  return { line: text.slice(start, stop).replace(/\r$/, ""), next: newline < 0 ? text.length : newline + 1 };
}

function skipBlankLines(text: string, start: number): number {
  let cursor = start;
  while (cursor < text.length) {
    const { line, next } = lineAt(text, cursor);
    if (line.trim() !== "" || next === cursor) break;
    cursor = next;
  }
  return cursor;
}

/** Where the frontmatter block starting at `start` ends, or null if none starts there. */
function blockEnd(text: string, start: number): number | null {
  if (start >= text.length) return null;
  const open = lineAt(text, start);
  if (open.line.trim() !== "---") return null;
  let cursor = open.next;
  let keyed = false;
  while (cursor < text.length) {
    const { line, next } = lineAt(text, cursor);
    if (line.trim() === "---") return keyed ? next : null;
    if (!keyed && line.trim() !== "") {
      if (!/^[A-Za-z_][\w-]*[ \t]*:/.test(line)) return null;
      keyed = true;
    }
    cursor = next;
  }
  return null;
}
