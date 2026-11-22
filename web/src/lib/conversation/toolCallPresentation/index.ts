/** A tool call reduced to everything its renderers need to show.
 *
 * Backends describe tools differently: protocol kinds, tool names, shell-wrapped
 * commands, structured arguments, and descriptive titles all arrive through the same
 * row. This module is the one place that turns those variants into one visible line,
 * one icon, and one disclosure decision.
 */

import { readableConversationDetail } from "../conversationDetail";
import type { ToolCallRow } from "../transcript";

/** How a call ended, as the person should read it. A search that found nothing is not a
 *  failure, even when the backend reports its exit status as one. */
export type ToolCallOutcome = "running" | "completed" | "failed" | "no_matches";

export type ToolCallPresentation = {
  iconPaths: readonly string[];
  /** What was done, in plain words: "Ran", "Edited", "Updated to-dos". */
  title: string;
  /** Which call it was: the command, the file, the pattern. Shortened to fit one line. */
  summary: string | null;
  /** The summary as it was given, when shortening it dropped something. */
  summaryInFull: string | null;
  /** Whether the summary is a literal thing (a command, a path, a pattern, an address)
   *  rather than words about the call ("0 of 5 done"). Only a literal is set as code. */
  summaryIsLiteral: boolean;
  /** Both halves as one line, for places with room for a single string. */
  sentence: string;
  detail: string | null;
  canExpand: boolean;
  outcome: ToolCallOutcome;
};

type ToolGlyphKind =
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

type ToolCallLine = {
  title: string;
  summary: string | null;
  summaryInFull?: string | null;
  /** The summary is the exact thing the call acted on, not a description of it. */
  literal?: boolean;
  /** What the row opens onto, when that is not the call's raw output. */
  detail?: string | null;
};

const TOOL_GLYPH_KINDS: readonly ToolGlyphKind[] = [
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
  move: ["M5 12h14", "m13 6 6 6-6 6"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14", "m21 21-4.3-4.3"],
  execute: ["m5 7 5 5-5 5", "M13 17h6"],
  think: [
    "M12 3a5 5 0 0 0-3 9c.6.5 1 1.2 1 2v1h4v-1c0-.8.4-1.5 1-2a5 5 0 0 0-3-9z",
    "M10 20h4"
  ],
  // A checklist: a to-do update is the list, not an edit to a file.
  todo: ["m4 6.5 1.5 1.5L8.5 5", "M12 6.5h8", "m4 13.5 1.5 1.5 3-3", "M12 13.5h8", "M5 20h.01", "M12 20h8"],
  // A globe: a fetch reaches out to an address, it is not a download to a file.
  fetch: ["M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18", "M3 12h18", "M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"],
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

/** The verb a row leads with when the call names its subject. The subject follows in the
 *  tool voice, so the verb alone reads as a sentence: "Ran npm test", "Read store.ts". */
const TOOL_CALL_PHRASES: Partial<Record<ToolGlyphKind, string>> = {
  read: "Read",
  edit: "Edited",
  delete: "Deleted",
  move: "Moved",
  search: "Searched for",
  execute: "Ran",
  fetch: "Fetched",
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
  // An agent may write both halves itself: "read: /path" or "search: pattern". Once
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

/** A backend that sends a call's arguments as its detail titles the row with the tool's own name. */
const TOOL_NAME_TITLE = /^[A-Za-z][\w-]*$/;

/** The word that names the tool: its own name when the call's arguments came as detail,
 *  otherwise the kind. Claude sends a normalized kind with the raw name as the title. */
function namedTool(row: ToolCallRow): string {
  const title = row.title.trim();
  if (title.toLowerCase() === row.toolKind.trim().toLowerCase()) return row.toolKind;
  const started = row.startedDetail?.trim() ?? "";
  return TOOL_NAME_TITLE.test(title) && callArguments(started) !== null
    ? title
    : row.toolKind;
}

function toolCallLine(row: ToolCallRow): ToolCallLine {
  // The start of a long output is not the output. A fragment can lose the shape the
  // rules below read — an object that no longer parses, a first line that was never the
  // whole of one — so a capped row's line is drawn from what is whole.
  const wholeDetail = typeof row.cappedDetailSequence === "number" ? null : row.detail;
  const known = knownToolLine(row, wholeDetail);
  if (known !== null) return known;
  // Both halves matter: the title says what happened and the summary says which call it
  // was. When a backend kind has no trustworthy phrase, its own title remains the fact.
  const tool = namedTool(row);
  const written = withoutTheKindItStated(
    commandWithoutShellInvocation(row.title.trim()),
    tool
  );
  const titleNamesTheTool = row.title.trim().toLowerCase() === tool.trim().toLowerCase();
  const kind = toolGlyphKind(tool);
  const phrase = TOOL_CALL_PHRASES[kind];
  const spoken =
    identifyingFact(row.startedDetail, wholeDetail) ??
    (titleNamesTheTool ? spokenDetail(row.startedDetail, wholeDetail) : written);
  const subject = spoken === null ? null : commandWithoutShellInvocation(spoken);
  const title = phrase !== undefined && subject !== null ? phrase : written;
  if (subject === null || addsNothingTo(subject, title)) return { title, summary: null };
  return subjectLine(title, subject, kind);
}

/** A subject made to fit one line without losing what identifies it.
 *
 * A path keeps its end, because the file's name is what a reader recognises and the
 * folders above the nearest two are where every call in the turn already was. An address
 * drops its scheme. Anything else keeps its start and says, with an ellipsis, that it
 * was cut. The whole subject stays with the line for anyone who wants it.
 */
/** The kinds whose subject is the literal thing acted on: a command, a path, a pattern, an
 *  address. Any other kind's subject is the backend's words about the call. */
const LITERAL_SUBJECT_KINDS: ReadonlySet<ToolGlyphKind> = new Set(["read", "edit", "delete", "move", "search", "execute", "fetch"]);

function subjectLine(title: string, subject: string, kind: ToolGlyphKind, literal = LITERAL_SUBJECT_KINDS.has(kind)): ToolCallLine {
  const whole = subject.trim();
  let compact = whole;
  if (kind === "read" || kind === "edit" || kind === "delete" || kind === "move") {
    compact = pathTail(whole);
  } else if (kind === "fetch") {
    compact = whole.replace(/^https?:\/\/(?:www\.)?/i, "");
  }
  const summary = shortened(compact);
  return { title, summary, summaryInFull: summary === whole ? null : whole, literal };
}

const PATH_SEGMENTS_KEPT = 2;

function pathTail(path: string): string {
  if (/\s/.test(path) || !/^(?:\/|~\/|[A-Za-z]:[\\/])/.test(path)) return path;
  const segments = path.split(/[\\/]/).filter((segment) => segment !== "");
  if (segments.length <= PATH_SEGMENTS_KEPT + 1) return path;
  return `…/${segments.slice(-PATH_SEGMENTS_KEPT).join("/")}`;
}

type TodoEntry = { content: string; status: string; activeForm?: string };

/** Lines for a checklist, marked by state, so an opened to-do update reads as the list. */
const TODO_MARKS: Record<string, string> = {
  completed: "✓",
  in_progress: "→",
  pending: "○"
};

function todoEntries(started: string | null): TodoEntry[] | null {
  const given = started === null ? null : callArguments(started.trim());
  const todos = given?.todos;
  if (!Array.isArray(todos)) return null;
  return todos.flatMap((todo): TodoEntry[] => {
    if (todo === null || typeof todo !== "object") return [];
    const { content, status, activeForm } = todo as Record<string, unknown>;
    if (typeof content !== "string") return [];
    return [{
      content,
      status: typeof status === "string" ? status : "pending",
      activeForm: typeof activeForm === "string" ? activeForm : undefined
    }];
  });
}

function todoLine(started: string | null): ToolCallLine {
  // The tool's own result only confirms the write ("Todos have been modified…"). What the
  // person wants is the list, so the list is what the row says and what it opens onto.
  const entries = todoEntries(started);
  if (entries === null || entries.length === 0) {
    return { title: "Updated to-dos", summary: null, detail: null };
  }
  const done = entries.filter((entry) => entry.status === "completed").length;
  const current = entries.find((entry) => entry.status === "in_progress");
  const counted = `${done} of ${entries.length} done`;
  return {
    title: "Updated to-dos",
    summary: shortened(current ? `${counted} · ${current.activeForm ?? current.content}` : counted),
    detail: entries
      .map((entry) => `${TODO_MARKS[entry.status] ?? TODO_MARKS.pending} ${entry.content}`)
      .join("\n")
  };
}

/** A Codex file change, titled "Change <path>" or "Change N files" with one
 *  "<kind> <path>" line per file as its detail. */
const CODEX_CHANGE_TITLE = /^Change (.+)$/;

function fileChangeLine(row: ToolCallRow, named: string): ToolCallLine {
  const changes = (row.startedDetail ?? row.detail ?? "")
    .split("\n")
    .map((line) => /^(add|update|delete|move)\s+(.+)$/.exec(line.trim()))
    .flatMap((match) => (match ? [{ kind: match[1], path: match[2] }] : []));
  const kinds = new Set(changes.map((change) => change.kind));
  const verb =
    kinds.size === 1 && kinds.has("add") ? "Created"
    : kinds.size === 1 && kinds.has("delete") ? "Deleted"
    : "Edited";
  const count = /^(\d+) files$/.exec(named);
  if (count === null) return subjectLine(verb, named, "edit");
  const names = changes.map((change) => change.path.split(/[\\/]/).at(-1) ?? change.path);
  return {
    title: `${verb} ${count[1]} files`,
    summary: names.length === 0 ? null : shortened(names.join(", ")),
    summaryInFull: names.length === 0 ? null : changes.map((change) => change.path).join(", "),
    literal: true
  };
}

/** Tools whose names say more than their kind, read by the name the backend gave them. */
function knownToolLine(row: ToolCallRow, wholeDetail: string | null): ToolCallLine | null {
  const title = row.title.trim();
  const change = CODEX_CHANGE_TITLE.exec(title);
  if (change && toolGlyphKind(row.toolKind) === "edit" && callArguments(row.startedDetail ?? "") === null) {
    return fileChangeLine(row, change[1]);
  }
  const tool = namedTool(row);
  const name = tool.trim().toLowerCase();
  const given = callArguments(row.startedDetail?.trim() ?? "");
  const named = (key: string): string | null => {
    const value = given?.[key];
    return typeof value === "string" && value.trim() !== "" ? firstLine(value) : null;
  };
  switch (name) {
    case "todowrite":
      return todoLine(row.startedDetail);
    case "write": {
      const path = named("file_path");
      if (path === null) return null;
      const created = /^File created successfully/i.test(wholeDetail?.trim() ?? "");
      return subjectLine(created ? "Created" : "Wrote", path, "edit");
    }
    case "glob":
    case "ls": {
      const pattern = named("pattern") ?? named("path");
      return pattern === null ? null : subjectLine("Listed files", pattern, "search");
    }
    case "websearch": {
      const query = named("query");
      return query === null ? null : subjectLine("Searched the web for", query, "search", false);
    }
    case "task":
    case "agent": {
      const description = named("description");
      return { title: "Ran a subagent", summary: description === null ? null : shortened(description) };
    }
    case "exitplanmode":
      return { title: "Presented a plan", summary: null };
  }
  const mcp = /^mcp__(.+?)__(.+)$/.exec(title);
  if (mcp) return { title: "Used", summary: shortened(`${mcp[1]} · ${mcp[2]}`), literal: true };
  return null;
}

/** Search programs that report "nothing found" with exit status 1 and no output. */
const SEARCH_PROGRAMS = /^(?:\S*\/)?(?:rg|grep|egrep|fgrep|ag|ack)$/;

/** Whether a failed call is really a search that found nothing.
 *
 * rg and grep exit 1 when nothing matches, and a backend that reports exit status as
 * success or failure turns that into a failed call. A real search error says what went
 * wrong on stderr, so a failure with no output at all from a command whose last step is
 * a search is the empty result, not a fault.
 */
function searchFoundNothing(row: ToolCallRow): boolean {
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
function lastStep(command: string): string {
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

function lineShowsWholeDetail(line: ToolCallLine, detail: string): boolean {
  // An expander that opens onto exactly what the closed line already says is an
  // affordance that does nothing.
  return addsNothingTo(detail, `${line.title} ${line.summaryInFull ?? line.summary ?? ""}`);
}

/** The leading verbs a tool call's sentence can begin with, and how each reads while the
 *  call is still under way. A title that does not begin with one of them (a backend's own
 *  tool name, say) is left as it was: nothing is guessed. */
const RUNNING_VERBS: Readonly<Record<string, string>> = {
  Ran: "Running",
  Read: "Reading",
  Edited: "Editing",
  Created: "Creating",
  Wrote: "Writing",
  Deleted: "Deleting",
  Moved: "Moving",
  Searched: "Searching",
  Listed: "Listing",
  Fetched: "Fetching",
  Switched: "Switching",
  Updated: "Updating",
  Presented: "Presenting",
  Used: "Using"
};

export function presentTense(sentence: string): string {
  const match = /^(\S+)(.*)$/s.exec(sentence);
  if (match === null) return sentence;
  const running = RUNNING_VERBS[match[1]!];
  return running === undefined ? sentence : `${running}${match[2]}`;
}

export function presentToolCall(row: ToolCallRow): ToolCallPresentation {
  // The renderers receive one coherent decision. Classification cannot choose one icon
  // while a caller independently chooses different wording or disclosure behavior.
  const line = toolCallLine(row);
  const noMatches = searchFoundNothing(row);
  const detail = line.detail !== undefined
    ? line.detail
    : noMatches ? null : readableConversationDetail(row.progress ?? row.detail);
  // A capped row holds the start of output the reader has not seen, so it opens whatever
  // that start happens to look like. Reading the prefix instead would let a row whose
  // first line repeats its title refuse to open onto the rest of the output.
  const capped = row.progress === null && typeof row.cappedDetailSequence === "number";
  // A call still under way says so in its verb ("Running npm test"), beside its spinner;
  // the past tense is for a call that has ended.
  const title = row.status === "running" ? presentTense(line.title) : line.title;
  return {
    iconPaths: STEP_ICON_PATHS[toolGlyphKind(namedTool(row))],
    title,
    summary: line.summary,
    summaryInFull: line.summaryInFull ?? null,
    summaryIsLiteral: line.summary !== null && line.literal === true,
    sentence: line.summary === null ? title : `${title} ${line.summary}`,
    detail,
    canExpand: (capped && line.detail === undefined)
      || (detail !== null && !lineShowsWholeDetail(line, detail)),
    outcome: noMatches ? "no_matches" : row.status
  };
}

/** A run of tool calls said as one quiet sentence, so the thread can keep the run closed
 *  until the person opens it on purpose: "Ran 4 commands, read 2 files". */
export type ToolRunSummary = {
  /** What the run did, counted by kind, in the past tense. */
  sentence: string;
  /** How many of its calls failed. A failure is never hidden by the fold: it is said. */
  failedCount: number;
  /** The call that is running now, when one is, so a live run can say its current step. */
  running: ToolCallPresentation | null;
  /** The glyph of the run's current or most frequent kind. */
  iconPaths: readonly string[];
};

const RUN_PHRASES: Partial<Record<ToolGlyphKind, (count: number) => string>> = {
  execute: (n) => `ran ${n} ${n === 1 ? "command" : "commands"}`,
  read: (n) => `read ${n} ${n === 1 ? "file" : "files"}`,
  edit: (n) => `edited ${n} ${n === 1 ? "file" : "files"}`,
  delete: (n) => `deleted ${n} ${n === 1 ? "file" : "files"}`,
  move: (n) => `moved ${n} ${n === 1 ? "file" : "files"}`,
  search: (n) => `searched ${n} ${n === 1 ? "time" : "times"}`,
  fetch: (n) => `fetched ${n} ${n === 1 ? "page" : "pages"}`,
  todo: () => "updated to-dos"
};
const RUN_PHRASES_SHOWN = 3;

function otherSteps(n: number): string {
  return `${n} other ${n === 1 ? "step" : "steps"}`;
}

/** Say a run of tool calls in one line. `total` is how many calls the run holds; when
 *  fewer of them are at hand than that, the line counts them rather than guess at kinds. */
export function summarizeToolRun(rows: readonly ToolCallRow[], total = rows.length): ToolRunSummary {
  const counted = new Map<ToolGlyphKind, number>();
  let failedCount = 0;
  let running: ToolCallRow | null = null;
  for (const row of rows) {
    const kind = toolGlyphKind(namedTool(row));
    counted.set(kind, (counted.get(kind) ?? 0) + 1);
    if (row.status === "failed" && !searchFoundNothing(row)) failedCount += 1;
    if (row.status === "running") running = row;
  }
  const runningPresentation = running === null ? null : presentToolCall(running);
  const busiest = [...counted.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "other";
  const iconPaths = runningPresentation?.iconPaths ?? STEP_ICON_PATHS[busiest];
  const callCount = Math.max(total, rows.length);
  if (rows.length < callCount || rows.length === 0) {
    return { sentence: `${callCount} tool ${callCount === 1 ? "call" : "calls"}`, failedCount, running: runningPresentation, iconPaths };
  }
  const phrases: string[] = [];
  let unnamed = 0;
  for (const [kind, count] of counted) {
    const phrase = RUN_PHRASES[kind];
    if (phrase === undefined || phrases.length >= RUN_PHRASES_SHOWN) unnamed += count;
    else phrases.push(phrase(count));
  }
  if (unnamed > 0) phrases.push(phrases.length === 0 ? `used ${unnamed} ${unnamed === 1 ? "tool" : "tools"}` : otherSteps(unnamed));
  const joined = phrases.length <= 1 ? phrases.join("") : `${phrases.slice(0, -1).join(", ")} and ${phrases.at(-1)}`;
  return { sentence: joined.charAt(0).toUpperCase() + joined.slice(1), failedCount, running: runningPresentation, iconPaths };
}
