import { describe, expect, it } from "vitest";
import { presentToolCall } from "../src/lib/conversation/toolCallPresentation";
import type { ToolCallRow } from "../src/lib/conversation/transcript";

function row(title: string, toolKind: string, startedDetail: string | null, detail: string | null): ToolCallRow {
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
    status: "completed",
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
    expect(presented.title).toBe("Ran command");
    expect(presented.summary).toBe("ls");
    expect(presented.iconPaths).toEqual(["m5 7 5 5-5 5", "M13 17h6"]);
  });
});
