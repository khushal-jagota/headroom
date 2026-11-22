import { describe, expect, it } from "vitest";
import { presentToolCall } from "../src/lib/conversation/toolCallPresentation";
import type { ToolCallRow } from "../src/lib/conversation/transcript";

function row(
  title: string,
  toolKind: string,
  startedDetail: string | null,
  detail: string | null,
  status: ToolCallRow["status"] = "completed"
): ToolCallRow {
  return {
    key: "e1",
    kind: "tool_call",
    sequence: 1,
    createdAt: 1,
    toolCallId: "call-1",
    title,
    toolKind,
    detail,
    startedDetail,
    cappedDetailSequence: null,
    status,
    progress: null
  };
}

describe("a Claude tool call sent with a normalized kind", () => {
  const cases: [string, string, string, string][] = [
    ["Bash", "execute", "{}", "done"],
    ["Bash", "execute", '{"command":"ls -la"}', "total 0"],
    ["TodoWrite", "think", '{"todos":[]}', "ok"],
    ["ExitPlanMode", "switch_mode", '{"plan":"x"}', "ok"],
    ["WebSearch", "search", '{"query":"svelte"}', "results"],
    ["mcp__github__search_code", "other", '{"q":"x"}', "hit"]
  ];

  it.each(cases)("%s presents as its raw-name row did", (name, kind, args, output) => {
    expect(presentToolCall(row(name, kind, args, output))).toEqual(
      presentToolCall(row(name, name, args, output))
    );
  });
});

describe("a Codex tool call", () => {
  it("keeps the kind path when its detail is a working directory", () => {
    const presented = presentToolCall(row("ls", "execute", "/repo", "/repo"));
    expect(presented.title).toBe("Ran");
    expect(presented.summary).toBe("ls");
    expect(presented.iconPaths).toEqual(["m5 7 5 5-5 5", "M13 17h6"]);
  });
});

// Every title below is one a driver really stores (rig/seed.ts mirrors
// server/drivers/*/runtime/events.ts). Each must read as a short sentence.
describe("a row's line reads as what was done to what", () => {
  const claude = (name: string, input: Record<string, unknown>, result: string | null) =>
    presentToolCall(row(name, name, JSON.stringify(input), result));

  it("unwraps a Codex login-shell command, even one the driver cut short", () => {
    expect(presentToolCall(row("bash -lc 'go test ./internal/push/...'", "execute", "/w", "ok"))).toMatchObject({
      title: "Ran", summary: "go test ./internal/push/...", sentence: "Ran go test ./internal/push/..."
    });
    expect(presentToolCall(row("bash -lc 'gh run list --limit 60 --json conclusion…", "execute", "/w", "x")).summary)
      .toBe("gh run list --limit 60 --json conclusion…");
  });

  it("keeps the end of an absolute path and the whole of it alongside", () => {
    const read = claude("Read", { file_path: "/srv/panels/server/conversation/record/data/store.ts" }, "1\tx");
    expect(read).toMatchObject({
      title: "Read",
      summary: "…/data/store.ts",
      summaryInFull: "/srv/panels/server/conversation/record/data/store.ts"
    });
    expect(claude("Edit", { file_path: "/a/b.ts" }, "updated").summary).toBe("/a/b.ts");
  });

  it("says a Write that made a file created it", () => {
    expect(claude("Write", { file_path: "/srv/p/web/src/x/search.ts" }, "File created successfully at: /srv/p/web/src/x/search.ts"))
      .toMatchObject({ title: "Created", summary: "…/x/search.ts" });
  });

  it("names a Codex file change by what changed rather than by its title", () => {
    const one = presentToolCall(row("Change Wren/Export/ExportV2Writer.swift", "edit", "add Wren/Export/ExportV2Writer.swift", "add Wren/Export/ExportV2Writer.swift"));
    expect(one).toMatchObject({ title: "Created", summary: "Wren/Export/ExportV2Writer.swift" });
    const two = "update Wren/Export/ExportImporter.swift\nupdate WrenTests/ExportTests.swift";
    expect(presentToolCall(row("Change 2 files", "edit", two, two))).toMatchObject({
      title: "Edited 2 files", summary: "ExportImporter.swift, ExportTests.swift"
    });
  });

  it("drops an address's scheme", () => {
    expect(claude("WebFetch", { url: "https://www.sqlite.org/fts5.html#the_bm25_function", prompt: "x" }, "y"))
      .toMatchObject({ title: "Fetched", summary: "sqlite.org/fts5.html#the_bm25_function" });
  });

  it("reads a search as a search for its pattern", () => {
    expect(claude("Grep", { pattern: "conversation_events", path: "/srv" }, "Found 12 files").title).toBe("Searched for");
  });

  it("names an MCP tool by its server and tool", () => {
    expect(presentToolCall(row("mcp__github__search_code", "other", '{"q":"x"}', "hit"))).toMatchObject({
      title: "Used", summary: "github · search_code"
    });
  });
});

describe("a to-do update", () => {
  const todos = {
    todos: [
      { content: "Schema step", status: "completed", activeForm: "Writing the schema step" },
      { content: "Ranking query", status: "in_progress", activeForm: "Writing the query" },
      { content: "Route", status: "pending", activeForm: "Adding the route" }
    ]
  };
  const presented = presentToolCall(row(
    "TodoWrite", "think", JSON.stringify(todos),
    "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress."
  ));

  it("reads as the list, never as the tool's confirmation", () => {
    expect(presented.title).toBe("Updated to-dos");
    expect(presented.summary).toBe("1 of 3 done · Writing the query");
    expect(presented.sentence).not.toContain("modified successfully");
    expect(presented.detail).toBe("✓ Schema step\n→ Ranking query\n○ Route");
    expect(presented.canExpand).toBe(true);
  });

  it("says its progress in words, not as code", () => {
    expect(presented.summaryIsLiteral).toBe(false);
  });

  it("has its own glyph, not the pencil of a file edit", () => {
    const edit = presentToolCall(row("Write", "Write", '{"file_path":"/repo/a.ts"}', "ok"));
    expect(presented.iconPaths).not.toEqual(edit.iconPaths);
    expect(presentToolCall(row("TodoWrite", "TodoWrite", "{}", "ok")).iconPaths).toEqual(presented.iconPaths);
  });

  it("still says what it did when the list did not come with it", () => {
    expect(presentToolCall(row("TodoWrite", "think", "{}", "Todos have been modified successfully."))).toMatchObject({
      title: "Updated to-dos", summary: null, detail: null, canExpand: false
    });
  });
});

describe("a search that found nothing", () => {
  it("is not a failure when it exits 1 with no output", () => {
    const codex = presentToolCall(row("bash -lc 'rg -n \"rate|limit\" server/share'", "execute", "/w", "", "failed"));
    expect(codex.outcome).toBe("no_matches");
    expect(codex.canExpand).toBe(false);
    const claude = presentToolCall(row("Bash", "execute", JSON.stringify({ command: "cd web && grep -rn SentrySDK src" }), "Exit code 1", "failed"));
    expect(claude.outcome).toBe("no_matches");
  });

  it("is still a failure when it said why, or when the search was not the last step", () => {
    expect(presentToolCall(row("rg x missing/", "execute", "/w", "rg: missing/: No such file or directory", "failed")).outcome).toBe("failed");
    expect(presentToolCall(row("rg x src | wc -l", "execute", "/w", "", "failed")).outcome).toBe("failed");
    expect(presentToolCall(row("npm run check", "execute", "/w", "", "failed")).outcome).toBe("failed");
  });

  it("leaves every other outcome as the backend reported it", () => {
    expect(presentToolCall(row("rg x", "execute", "/w", "a:1", "completed")).outcome).toBe("completed");
    expect(presentToolCall(row("rg x", "execute", "/w", null, "running")).outcome).toBe("running");
  });
});

describe("which summaries are set as code", () => {
  it("sets a command, a path and a pattern as code", () => {
    expect(presentToolCall(row("Bash", "execute", '{"command":"git rm web/a.ts"}', "")).summaryIsLiteral).toBe(true);
    expect(presentToolCall(row("Read", "read", '{"file_path":"/repo/a/b/c.ts"}', "x")).summaryIsLiteral).toBe(true);
    expect(presentToolCall(row("Grep", "search", '{"pattern":"conversation_events"}', "x")).summaryIsLiteral).toBe(true);
  });

  it("sets words about a call as words", () => {
    expect(presentToolCall(row("WebSearch", "search", '{"query":"svelte runes"}', "results")).summaryIsLiteral).toBe(false);
    expect(presentToolCall(row("Task", "think", '{"description":"Find the flaky test"}', "ok")).summaryIsLiteral).toBe(false);
  });

  it("is never literal without a summary", () => {
    expect(presentToolCall(row("TodoWrite", "think", "{}", "ok")).summaryIsLiteral).toBe(false);
  });
});
