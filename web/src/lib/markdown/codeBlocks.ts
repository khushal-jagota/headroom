/** A fenced code block drawn as a first-class block: the language it was fenced with, a
 * copy button that copies exactly the code, and quiet syntax colour.
 *
 * Colour never touches the DOM of the code itself. The text nodes the Markdown renderer
 * made stay exactly as they were, because text locations, selection copy and restoration
 * all address those nodes. Colour is laid over them with the CSS Custom Highlight API,
 * and a browser without it shows the same code uncoloured. */
import "./codeBlocks.css";

export type CodeTokenKind = "comment" | "string" | "number" | "keyword" | "literal" | "function" | "type" | "inserted" | "deleted";
export type CodeToken = { kind: CodeTokenKind; start: number; end: number };

type Family = "c" | "hash" | "sql" | "json" | "diff";

const C_KEYWORDS = new Set(("abstract as async await break case catch class const continue debugger declare default defer delete do else enum export extends " +
  "final finally fn for func function go goto if impl implements import in instanceof interface internal let loop match mod module move mut namespace new " +
  "override package private protected pub public readonly return satisfies select static struct super switch throw throws trait try type typeof unsafe " +
  "use var void where while with yield guard extension protocol init deinit fileprivate open some any is val fun object when lazy").split(" "));
const HASH_KEYWORDS = new Set(("and as assert async await break case class continue def del do done elif else end esac except export fi finally for from " +
  "function global if import in is lambda local module nonlocal not or pass raise readonly rescue return then unless until while with yield begin ensure " +
  "require self match").split(" "));
const SQL_KEYWORDS = new Set(("add alter and as asc begin between by case check commit conflict constraint create cross default delete desc distinct do drop " +
  "else end exists foreign from full group having if in index inner insert into is join key left like limit match not null offset on or order outer " +
  "over partition primary references replace returning right rollback select set table then transaction trigger union unique update using values " +
  "view virtual when where with without").split(" "));
const LITERALS = new Set(["true", "false", "null", "undefined", "nil", "None", "True", "False", "NaN", "Infinity", "this", "self"]);

const FAMILIES: Record<string, Family> = {};
for (const name of ["js", "jsx", "mjs", "cjs", "javascript", "ts", "tsx", "mts", "typescript", "java", "c", "h", "cpp", "c++", "cc", "hpp", "cs", "csharp",
  "go", "golang", "rust", "rs", "swift", "kotlin", "kt", "scala", "dart", "php", "svelte", "objc", "objective-c", "zig", "groovy", "proto", "protobuf"]) FAMILIES[name] = "c";
for (const name of ["py", "python", "rb", "ruby", "sh", "bash", "zsh", "shell", "console", "shellsession", "fish", "yaml", "yml", "toml", "perl", "pl",
  "r", "dockerfile", "docker", "makefile", "make", "ini", "conf", "nix", "elixir", "ex", "exs", "powershell", "ps1"]) FAMILIES[name] = "hash";
for (const name of ["sql", "sqlite", "postgres", "postgresql", "psql", "mysql"]) FAMILIES[name] = "sql";
for (const name of ["json", "jsonc", "json5", "jsonl", "ndjson"]) FAMILIES[name] = "json";
for (const name of ["diff", "patch"]) FAMILIES[name] = "diff";

/** Above this, a block is left uncoloured: it stays readable and costs nothing. */
const HIGHLIGHT_LIMIT = 60_000;

/** The language a fence named, from the `language-…` class the renderer keeps. */
export function codeLanguage(code: Element | null): string | null {
  if (!code) return null;
  for (const name of Array.from(code.classList)) {
    const match = /^language-([\w+#.-]+)$/.exec(name);
    if (match) return match[1].toLowerCase();
  }
  return null;
}

/** Where each coloured stretch of `text` starts and ends. Unknown languages get none. */
export function tokenizeCode(text: string, language: string | null): CodeToken[] {
  const family = language === null ? undefined : FAMILIES[language];
  if (family === undefined || text.length > HIGHLIGHT_LIMIT) return [];
  if (family === "diff") return diffTokens(text);
  const tokens: CodeToken[] = [];
  const push = (kind: CodeTokenKind, start: number, end: number) => { if (end > start) tokens.push({ kind, start, end }); };
  const lineEnd = (from: number) => { const at = text.indexOf("\n", from); return at < 0 ? text.length : at; };
  const backtickStrings = language !== null && ["js", "jsx", "mjs", "cjs", "javascript", "ts", "tsx", "mts", "typescript", "go", "golang", "sh", "bash", "zsh", "shell", "svelte"].includes(language);
  const typed = family === "c";
  const keywords = family === "c" ? C_KEYWORDS : family === "hash" ? HASH_KEYWORDS : family === "sql" ? SQL_KEYWORDS : null;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    const next = text[i + 1];
    const previous = i > 0 ? text[i - 1] : "\n";
    // Comments.
    if ((family === "c" || family === "json") && ch === "/" && next === "/") { const end = lineEnd(i); push("comment", i, end); i = end; continue; }
    if ((family === "c" || family === "sql" || family === "json") && ch === "/" && next === "*") {
      const close = text.indexOf("*/", i + 2); const end = close < 0 ? text.length : close + 2; push("comment", i, end); i = end; continue;
    }
    if (family === "hash" && ch === "#" && /\s/.test(previous)) { const end = lineEnd(i); push("comment", i, end); i = end; continue; }
    if (family === "sql" && ch === "-" && next === "-") { const end = lineEnd(i); push("comment", i, end); i = end; continue; }
    // Strings. Triple quotes and backticks may span lines; the rest stop at the line's end.
    if (family === "hash" && (ch === '"' || ch === "'") && text.startsWith(ch.repeat(3), i)) {
      const close = text.indexOf(ch.repeat(3), i + 3); const end = close < 0 ? text.length : close + 3; push("string", i, end); i = end; continue;
    }
    if (ch === '"' || (ch === "'" && family !== "json") || (ch === "`" && backtickStrings)) {
      if (ch === "'" && (language === "rust" || language === "rs")) {
        // A Rust quote is a lifetime unless it closes a single character.
        const literal = /^'(?:\\.[^']*|[^\\'\n])'/.exec(text.slice(i, i + 12));
        if (!literal) { i += 1; continue; }
        push("string", i, i + literal[0].length); i += literal[0].length; continue;
      }
      let j = i + 1;
      while (j < text.length && text[j] !== ch && (ch === "`" || text[j] !== "\n")) j += text[j] === "\\" ? 2 : 1;
      const end = Math.min(text.length, j + 1);
      push("string", i, end); i = end; continue;
    }
    // Numbers, only where a word does not continue into them.
    if (/[0-9]/.test(ch) && !/[\w$]/.test(previous)) {
      const number = /^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)[a-zA-Z]*/.exec(text.slice(i, i + 64));
      const length = number ? number[0].length : 1;
      push("number", i, i + length); i += length; continue;
    }
    // Words.
    if (/[A-Za-z_$]/.test(ch)) {
      let j = i + 1;
      while (j < text.length && /[\w$]/.test(text[j])) j += 1;
      const word = text.slice(i, j);
      const member = previous === ".";
      if (family === "json") { if (LITERALS.has(word)) push("literal", i, j); }
      else if (!member && LITERALS.has(word)) push("literal", i, j);
      else if (!member && keywords !== null && keywords.has(family === "sql" ? word.toLowerCase() : word)) push("keyword", i, j);
      else if (family !== "sql" && /^\s*\(/.test(text.slice(j, j + 8))) push("function", i, j);
      else if (typed && /^[A-Z][a-z0-9]\w*$/.test(word)) push("type", i, j);
      i = j; continue;
    }
    i += 1;
  }
  return tokens;
}

function diffTokens(text: string): CodeToken[] {
  const tokens: CodeToken[] = [];
  let start = 0;
  for (const line of text.split("\n")) {
    const end = start + line.length;
    if (line.startsWith("+") && !line.startsWith("+++")) tokens.push({ kind: "inserted", start, end });
    else if (line.startsWith("-") && !line.startsWith("---")) tokens.push({ kind: "deleted", start, end });
    else if (line.startsWith("@@")) tokens.push({ kind: "comment", start, end });
    start = end + 1;
  }
  return tokens.filter(token => token.end > token.start);
}

function highlightRegistry(): HighlightRegistry | null {
  return typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight === "function" ? CSS.highlights : null;
}

/** Lay colour over the code's own text nodes. Returns how to take it off again. */
function highlightCode(code: HTMLElement, language: string | null): () => void {
  const registry = highlightRegistry();
  if (registry === null) return () => {};
  const text = code.textContent ?? "";
  const tokens = tokenizeCode(text, language);
  if (tokens.length === 0) return () => {};
  const nodes: { node: Text; start: number }[] = [];
  const walker = code.ownerDocument.createTreeWalker(code, NodeFilter.SHOW_TEXT);
  let offset = 0;
  for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
    nodes.push({ node, start: offset }); offset += node.data.length;
  }
  const locate = (at: number, end: boolean): [Text, number] | null => {
    for (let index = 0; index < nodes.length; index++) {
      const { node, start } = nodes[index];
      const limit = start + node.data.length;
      if (end ? at <= limit : at < limit) return [node, at - start];
    }
    return null;
  };
  const added: { highlight: Highlight; range: Range }[] = [];
  for (const token of tokens) {
    const from = locate(token.start, false);
    const to = locate(token.end, true);
    if (!from || !to) continue;
    const range = code.ownerDocument.createRange();
    range.setStart(from[0], from[1]); range.setEnd(to[0], to[1]);
    const name = `panels-code-${token.kind}`;
    let highlight = registry.get(name);
    if (!highlight) { highlight = new Highlight(); registry.set(name, highlight); }
    pruneDetached(highlight);
    highlight.add(range);
    added.push({ highlight, range });
  }
  return () => { for (const { highlight, range } of added) highlight.delete(range); };
}

/** A safety net for surfaces dropped without their teardown: forget ranges over text
 *  that is no longer in any document, so nothing outlives the code it coloured. */
function pruneDetached(highlight: Highlight): void {
  if (highlight.size < 2000) return;
  for (const range of [...highlight]) {
    if (range instanceof Range && !range.startContainer.isConnected) highlight.delete(range);
  }
}

/** The words a copy should hold: the code, without the fence's closing line break. */
export function codeCopyText(raw: string): string {
  return raw.endsWith("\n") ? raw.slice(0, -1) : raw;
}

const COPIED_MS = 1600;

/** Put a header on one rendered `<pre>`: its language and a copy button. The `<pre>` and
 *  every text node inside it are kept, moved into a frame rather than rebuilt. */
export function presentCodeBlock(pre: HTMLElement): { frame: HTMLElement; stop: () => void } {
  const doc = pre.ownerDocument;
  const code = pre.querySelector<HTMLElement>(":scope > code");
  const language = codeLanguage(code);
  const frame = doc.createElement("div");
  frame.className = "markdown-code";
  frame.dataset.markdownCode = "";
  const head = doc.createElement("div");
  head.className = "markdown-code-head";
  // Chrome, not content: a selection copy across the block leaves it out.
  head.dataset.markdownChrome = "";
  const label = doc.createElement("span");
  label.className = "markdown-code-language";
  label.textContent = language ?? "";
  const copy = doc.createElement("button");
  copy.type = "button";
  copy.className = "button button--ghost markdown-code-copy";
  copy.textContent = "Copy";
  copy.title = "Copy this code";
  head.append(label, copy);
  pre.replaceWith(frame);
  frame.append(head, pre);
  let timer: ReturnType<typeof setTimeout> | null = null;
  const settle = (words: string) => {
    copy.textContent = words;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; copy.textContent = "Copy"; }, COPIED_MS);
  };
  const onCopy = (): void => {
    const text = codeCopyText((code ?? pre).textContent ?? "");
    const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
    const selectCode = () => {
      // Without clipboard access the code is selected, so the keyboard can copy it.
      const selection = doc.getSelection();
      if (!selection) return;
      const range = doc.createRange(); range.selectNodeContents(code ?? pre);
      selection.removeAllRanges(); selection.addRange(range);
    };
    if (!clipboard?.writeText) { selectCode(); settle("Selected"); return; }
    clipboard.writeText(text).then(() => settle("Copied"), () => { selectCode(); settle("Selected"); });
  };
  copy.addEventListener("click", onCopy);
  const unhighlight = code ? highlightCode(code, language) : () => {};
  return {
    frame,
    stop: () => {
      copy.removeEventListener("click", onCopy);
      if (timer !== null) clearTimeout(timer);
      unhighlight();
    }
  };
}
