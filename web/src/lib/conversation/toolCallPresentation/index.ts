/** A tool call reduced to everything its renderers need to show.
 *
 * Backends describe tools differently: protocol kinds, tool names, shell-wrapped
 * commands, structured arguments, and descriptive titles all arrive through the same
 * row. This module is the one place that turns those variants into one visible line,
 * one icon, and one disclosure decision.
 */

import { readableConversationDetail } from "../conversationDetail";
import type { ToolCallRow } from "../transcript";

export type ToolCallPresentation = {
  iconPaths: readonly string[];
  title: string;
  summary: string | null;
  detail: string | null;
  canExpand: boolean;
};

type ToolGlyphKind =
  | "read"
  | "edit"
  | "delete"
  | "move"
  | "search"
  | "execute"
  | "think"
  | "fetch"
  | "switch_mode"
  | "other";

type ToolCallLine = {
  title: string;
  summary: string | null;
};

const TOOL_GLYPH_KINDS: readonly ToolGlyphKind[] = [
  "read",
  "edit",
  "delete",
  "move",
  "search",
  "execute",
  "think",
  "fetch",
  "switch_mode",
  "other"
];

/** Kind glyphs for tool-call steps, drawn as 24×24 stroke icons. */
const STEP_ICON_PATHS: Record<ToolGlyphKind, readonly string[]> = {
  read: [
    "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z",
    "M14 3v5h5"
  ],
  edit: ["M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"],
  delete: [
    "M4 7h16",
    "M9 7V4h6v3",
    "M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13",
    "M10 11v6",
    "M14 11v6"
  ],
  move: ["M9 7h8v8", "M7 17 17 7"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14", "m21 21-4.3-4.3"],
  execute: ["m5 7 5 5-5 5", "M13 17h6"],
  think: [
    "M12 3a5 5 0 0 0-3 9c.6.5 1 1.2 1 2v1h4v-1c0-.8.4-1.5 1-2a5 5 0 0 0-3-9z",
    "M10 20h4"
  ],
  fetch: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  switch_mode: ["M4 9h13", "m14 6 3 3-3 3", "M20 15H7", "m10 12-3 3 3 3"],
  other: ["M6 12h.01", "M12 12h.01", "M18 12h.01"]
};

/** What a tool did, read from what it is called.
 *
 * Backends do not agree on this field: one sends protocol kinds and another sends the
 * tool's name. Both are interpreted by meaning rather than by which backend sent them.
 * An unrecognised word gets the neutral glyph instead of a guess.
 */
const TOOL_NAME_HINTS: readonly (readonly [ToolGlyphKind, readonly string[]])[] = [
  ["execute", ["bash", "shell", "exec", "command", "terminal", "process"]],
  ["search", ["search", "grep", "glob", "find", "list", "ls"]],
  ["fetch", ["fetch", "http", "curl", "download", "web"]],
  ["edit", ["edit", "write", "patch", "apply", "update", "create", "notebook"]],
  ["read", ["read", "view", "open", "cat"]],
  ["delete", ["delete", "remove", "trash"]],
  ["move", ["move", "rename"]],
  ["think", ["think", "plan", "task", "todo", "reason"]]
];

const TOOL_CALL_PHRASES: Partial<Record<ToolGlyphKind, string>> = {
  read: "Read file",
  edit: "Edited file",
  delete: "Deleted file",
  move: "Moved file",
  search: "Searched files",
  execute: "Ran command",
  fetch: "Fetched page",
  switch_mode: "Switched mode"
};

/** Argument names that identify a call's subject, best first.
 *
 * Claude sends a call's arguments as detail. Only these named fields are subjects;
 * falling back to an arbitrary value could put a payload or secret into a conversation
 * line. The start detail is preferred because a finish usually carries output instead.
 */
const CALL_SUBJECT_ARGUMENT_NAMES: readonly string[] = [
  "command",
  "file_path",
  "notebook_path",
  "pattern",
  "url",
  "query",
  "subject",
  "description"
];

/** Shells a command arrives wrapped in, each up to the flag that carries it.
 *
 * The wrapper is how the command was carried. The command inside it is what the person
 * needs to read, so Codex's repeated login-shell prefix does not become every row's title.
 */
const SHELL_INVOCATIONS: readonly RegExp[] = [
  /^(?:\S*\/)?(?:bash|sh|zsh)\s+-[a-z]*c\s+([\s\S]+)$/,
  /^(?:\S*[/\\])?cmd(?:\.exe)?\s+\/c\s+([\s\S]+)$/i,
  /^(?:\S*[/\\])?pwsh(?:\.exe)?\s+-(?:command|c)\s+([\s\S]+)$/i
];

const TOOL_KIND_WORDS: ReadonlySet<string> = new Set([
  "read",
  "write",
  "edit",
  "delete",
  "move",
  "search",
  "grep",
  "glob",
  "execute",
  "terminal",
  "command",
  "shell",
  "bash",
  "fetch"
]);

const TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS = 80;

function toolGlyphKind(toolKind: string): ToolGlyphKind {
  const named = toolKind.trim().toLowerCase();
  const exact = TOOL_GLYPH_KINDS.find((kind) => kind === named);
  if (exact !== undefined) return exact;
  for (const [kind, hints] of TOOL_NAME_HINTS) {
    if (hints.some((hint) => named.includes(hint))) return kind;
  }
  return "other";
}

function commandWithoutShellInvocation(text: string): string {
  const trimmed = text.trim();
  for (const invocation of SHELL_INVOCATIONS) {
    const wrapped = invocation.exec(trimmed);
    if (wrapped) return unquoted(wrapped[1].trim());
  }
  return text;
}

function unquoted(text: string): string {
  const quote = text[0];
  if ((quote !== '"' && quote !== "'") || text.length < 2 || !text.endsWith(quote)) {
    return text;
  }
  const inside = text.slice(1, -1);
  return quote === '"' ? inside.replace(/\\(["\\])/g, "$1") : inside;
}

function identifyingFact(
  startedDetail: string | null,
  detail: string | null
): string | null {
  // A backend that sends arguments says which call this was with one named value inside
  // that object. Read the start before the finish so output never replaces identity.
  for (const written of [startedDetail, detail]) {
    if (written === null) continue;
    const trimmed = written.trim();
    if (trimmed === "") continue;
    const given = callArguments(trimmed);
    if (given === null) continue;
    const subject = subjectOf(given);
    if (subject !== null) return subject;
  }
  return null;
}

function spokenDetail(
  startedDetail: string | null,
  detail: string | null
): string | null {
  // A single line may be the backend describing the call. Several lines are output and
  // belong behind the row; an argument object is interpreted by identifyingFact instead.
  for (const written of [startedDetail, detail]) {
    if (written === null) continue;
    const trimmed = written.trim();
    if (trimmed === "" || trimmed.includes("\n") || callArguments(trimmed) !== null) {
      continue;
    }
    return trimmed;
  }
  return null;
}

function withoutTheKindItStated(text: string, toolKind: string): string {
  // Hermes often writes both halves itself: "read: /path" or "search: pattern". Once
  // the visible title says the kind, the repeated prefix adds nothing to the summary.
  const stated = /^([\p{L}_]+)\s*:\s*(\S[\s\S]*)$/u.exec(text);
  if (!stated) return text;
  const front = stated[1].toLowerCase();
  const kindsItCouldBe = new Set<string>([
    toolKind.trim().toLowerCase(),
    toolGlyphKind(toolKind)
  ]);
  return kindsItCouldBe.has(front) || TOOL_KIND_WORDS.has(front)
    ? stated[2].trim()
    : text;
}

function callArguments(trimmed: string): Record<string, unknown> | null {
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

function subjectOf(given: Record<string, unknown>): string | null {
  for (const name of CALL_SUBJECT_ARGUMENT_NAMES) {
    const value = given[name];
    if (typeof value !== "string") continue;
    const said = firstLine(value);
    if (said !== "") return said;
  }
  return null;
}

function firstLine(text: string): string {
  const at = text.indexOf("\n");
  return (at === -1 ? text : text.slice(0, at)).trim();
}

function shortened(text: string): string {
  // A tool summary is always one visible line. The ellipsis says when a subject did not
  // fit rather than silently ending it at an arbitrary character.
  return text.length <= TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS
    ? text
    : `${text.slice(0, TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS - 1).trimEnd()}…`;
}

function words(text: string): string[] {
  // Compare meaning by whole words so "Searched" does not swallow "arch", and "Read"
  // does not swallow a file called "edit.ts".
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

function addsNothingTo(said: string, within: string): boolean {
  // The sought words must already occur together and in order. Scattered words are not
  // the same fact, and a substring is not necessarily the same word.
  const sought = words(said);
  const already = words(within);
  if (sought.length === 0) return true;
  if (sought.length > already.length) return false;
  for (let from = 0; from <= already.length - sought.length; from += 1) {
    if (sought.every((word, at) => already[from + at] === word)) return true;
  }
  return false;
}

function toolCallLine(row: ToolCallRow): ToolCallLine {
  // Both halves matter: the title says what happened and the summary says which call it
  // was. When a backend kind has no trustworthy phrase, its own title remains the fact.
  const written = withoutTheKindItStated(
    commandWithoutShellInvocation(row.title.trim()),
    row.toolKind
  );
  const titleNamesTheTool =
    row.title.trim().toLowerCase() === row.toolKind.trim().toLowerCase();
  const phrase = TOOL_CALL_PHRASES[toolGlyphKind(row.toolKind)];
  const spoken =
    identifyingFact(row.startedDetail, row.detail) ??
    (titleNamesTheTool ? spokenDetail(row.startedDetail, row.detail) : written);
  const summary =
    spoken === null ? null : shortened(commandWithoutShellInvocation(spoken));
  const title = phrase !== undefined && summary !== null ? phrase : written;
  return {
    title,
    summary: summary !== null && addsNothingTo(summary, title) ? null : summary
  };
}

function lineShowsWholeDetail(line: ToolCallLine, detail: string): boolean {
  // An expander that opens onto exactly what the closed line already says is an
  // affordance that does nothing.
  return addsNothingTo(detail, `${line.title} ${line.summary ?? ""}`);
}

export function presentToolCall(row: ToolCallRow): ToolCallPresentation {
  // The renderers receive one coherent decision. Classification cannot choose one icon
  // while a caller independently chooses different wording or disclosure behavior.
  const line = toolCallLine(row);
  const detail = readableConversationDetail(row.progress ?? row.detail);
  return {
    iconPaths: STEP_ICON_PATHS[toolGlyphKind(row.toolKind)],
    title: line.title,
    summary: line.summary,
    detail,
    canExpand: detail !== null && !lineShowsWholeDetail(line, detail)
  };
}
