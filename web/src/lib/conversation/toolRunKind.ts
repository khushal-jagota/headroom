/** What kind of work a tool call did, read from the call alone.
 *
 * The browser and the server both read this one rule: the browser to draw a call and to
 * say a run of calls in one line, the server to count a closed run's calls by kind without
 * sending them. It imports nothing, so the server can load it as it is.
 */

/** The fields of a tool call that say what kind of work it did. */
export type ToolRunCall = {
  title: string;
  toolKind: string;
  detail: string | null;
  startedDetail: string | null;
  cappedDetailSequence: number | null;
  status: "running" | "completed" | "failed";
};

export type ToolGlyphKind =
  | "read"
  | "edit"
  | "delete"
  | "move"
  | "search"
  | "execute"
  | "think"
  | "todo"
  | "fetch"
  | "switch_mode"
  | "other";

export const TOOL_GLYPH_KINDS: readonly ToolGlyphKind[] = [
  "read",
  "edit",
  "delete",
  "move",
  "search",
  "execute",
  "think",
  "todo",
  "fetch",
  "switch_mode",
  "other"
];

/** What a tool did, read from what it is called.
 *
 * Backends do not agree on this field: one sends protocol kinds and another sends the
 * tool's name. Both are interpreted by meaning rather than by which backend sent them.
 * An unrecognised word gets the neutral glyph instead of a guess.
 */
export const TOOL_NAME_HINTS: readonly (readonly [ToolGlyphKind, readonly string[]])[] = [
  // First, because a to-do tool's name also says what it does to the list ("TodoWrite").
  ["todo", ["todo"]],
  ["execute", ["bash", "shell", "exec", "command", "terminal", "process"]],
  ["search", ["search", "grep", "glob", "find", "list", "ls"]],
  ["fetch", ["fetch", "http", "curl", "download", "web"]],
  ["edit", ["edit", "write", "patch", "apply", "update", "create", "notebook"]],
  ["read", ["read", "view", "open", "cat"]],
  ["delete", ["delete", "remove", "trash"]],
  ["move", ["move", "rename"]],
  ["think", ["think", "plan", "task", "reason"]]
];

/** Shells a command arrives wrapped in, each up to the flag that carries it.
 *
 * The wrapper is how the command was carried. The command inside it is what the person
 * needs to read, so Codex's repeated login-shell prefix does not become every row's title.
 */
export const SHELL_INVOCATIONS: readonly RegExp[] = [
  /^(?:\S*\/)?(?:bash|sh|zsh)\s+-[a-z]*c\s+([\s\S]+)$/,
  /^(?:\S*[/\\])?cmd(?:\.exe)?\s+\/c\s+([\s\S]+)$/i,
  /^(?:\S*[/\\])?pwsh(?:\.exe)?\s+-(?:command|c)\s+([\s\S]+)$/i
];

export function toolGlyphKind(toolKind: string): ToolGlyphKind {
  const named = toolKind.trim().toLowerCase();
  const exact = TOOL_GLYPH_KINDS.find((kind) => kind === named);
  if (exact !== undefined) return exact;
  for (const [kind, hints] of TOOL_NAME_HINTS) {
    if (hints.some((hint) => named.includes(hint))) return kind;
  }
  return "other";
}

export function commandWithoutShellInvocation(text: string): string {
  const trimmed = text.trim();
  for (const invocation of SHELL_INVOCATIONS) {
    const wrapped = invocation.exec(trimmed);
    if (wrapped) return unquoted(wrapped[1].trim());
  }
  return text;
}

export function unquoted(text: string): string {
  const quote = text[0];
  if (quote !== '"' && quote !== "'") return text;
  // A backend that caps its titles cuts the closing quote off with the rest. The opening
  // one is still only how the command was carried.
  if (text.endsWith("…") && !text.slice(1).includes(quote)) return text.slice(1);
  if (text.length < 2 || !text.endsWith(quote)) {
    return text;
  }
  const inside = text.slice(1, -1);
  return quote === '"' ? inside.replace(/\\(["\\])/g, "$1") : inside;
}

export function callArguments(trimmed: string): Record<string, unknown> | null {
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** A backend that sends a call's arguments as its detail titles the row with the tool's own name. */
export const TOOL_NAME_TITLE = /^[A-Za-z][\w-]*$/;

/** The word that names the tool: its own name when the call's arguments came as detail,
 *  otherwise the kind. Claude sends a normalized kind with the raw name as the title. */
export function namedTool(row: ToolRunCall): string {
  const title = row.title.trim();
  if (title.toLowerCase() === row.toolKind.trim().toLowerCase()) return row.toolKind;
  const started = row.startedDetail?.trim() ?? "";
  return TOOL_NAME_TITLE.test(title) && callArguments(started) !== null
    ? title
    : row.toolKind;
}

/** Search programs that report "nothing found" with exit status 1 and no output. */
export const SEARCH_PROGRAMS = /^(?:\S*\/)?(?:rg|grep|egrep|fgrep|ag|ack)$/;

/** Whether a failed call is really a search that found nothing.
 *
 * rg and grep exit 1 when nothing matches, and a backend that reports exit status as
 * success or failure turns that into a failed call. A real search error says what went
 * wrong on stderr, so a failure with no output at all from a command whose last step is
 * a search is the empty result, not a fault.
 */
export function searchFoundNothing(row: ToolRunCall): boolean {
  if (row.status !== "failed" || toolGlyphKind(namedTool(row)) !== "execute") return false;
  if (row.cappedDetailSequence !== null) return false;
  const output = row.detail?.trim() ?? "";
  const startedArguments = callArguments(row.startedDetail?.trim() ?? "");
  // Claude's Bash reports the bare exit status as the result of a silent failure.
  if (output !== "" && output !== row.startedDetail?.trim() && !/^(?:Error: )?Exit code 1$/.test(output)) {
    return false;
  }
  const command = typeof startedArguments?.command === "string"
    ? startedArguments.command
    : row.title;
  const last = lastStep(commandWithoutShellInvocation(command.trim())).split(/\s+/);
  const program = last[0] === "git" && last[1] === "grep" ? "grep" : last[0] ?? "";
  return SEARCH_PROGRAMS.test(program);
}

/** The last step of a shell line: what follows its final unquoted `;`, `&&`, `||` or `|`,
 *  which is the step whose exit status the whole line reports. */
export function lastStep(command: string): string {
  let quote: string | null = null;
  let from = 0;
  for (let at = 0; at < command.length; at += 1) {
    const character = command[at];
    if (quote !== null) {
      if (character === "\\" && quote === '"') at += 1;
      else if (character === quote) quote = null;
    } else if (character === "\\") {
      at += 1;
    } else if (character === '"' || character === "'") {
      quote = character;
    } else if (character === ";" || character === "|" || (character === "&" && command[at + 1] === "&")) {
      if (command[at + 1] === character) at += 1;
      from = at + 1;
    }
  }
  return command.slice(from).trim();
}

/** A run's calls counted by kind, in the order each kind first appears, and the calls
 *  that failed. A search that found nothing is not a failure. */
export type ToolRunCounts = {
  kinds: Array<{ kind: ToolGlyphKind; count: number }>;
  failedCount: number;
};

export function countToolRun(calls: readonly ToolRunCall[]): ToolRunCounts {
  const counted = new Map<ToolGlyphKind, number>();
  let failedCount = 0;
  for (const call of calls) {
    const kind = toolGlyphKind(namedTool(call));
    counted.set(kind, (counted.get(kind) ?? 0) + 1);
    if (call.status === "failed" && !searchFoundNothing(call)) failedCount += 1;
  }
  return { kinds: [...counted].map(([kind, count]) => ({ kind, count })), failedCount };
}
