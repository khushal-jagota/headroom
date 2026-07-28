import { describe, expect, it } from "vitest";

import {
  commandWithoutShellInvocation,
  lineShowsWholeDetail,
  promptLabelFor,
  readableDetail,
  toolCallLine,
  toolGlyphKind
} from "../src/lib/conversation/transcript";

describe("Conversation tool presentation", () => {
  it("lays out structured detail and leaves prose unchanged", () => {
    expect(readableDetail('{"a":1,"b":[2,3]}')).toBe(
      '{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}'
    );
    expect(readableDetail("ls -la /tmp")).toBe("ls -la /tmp");
    expect(readableDetail("{not actually json")).toBe("{not actually json");
    expect(readableDetail("")).toBeNull();
    expect(readableDetail(null)).toBeNull();
    expect(readableDetail(undefined)).toBeNull();
  });

  it("labels only messages from somebody else", () => {
    expect(promptLabelFor("owner", "owner")).toBeNull();
    expect(promptLabelFor("automatic loop", "owner")).toBe("automatic loop");
    expect(promptLabelFor("owner", null)).toBe("owner");
  });

  it.each([
    ["read", "read"],
    ["switch_mode", "switch_mode"],
    ["Bash", "execute"],
    ["Grep", "search"],
    ["WebFetch", "fetch"],
    ["NotebookEdit", "edit"],
    ["Read", "read"],
    ["something unknown", "other"]
  ] as const)("classifies %s with the %s glyph", (toolKind, expected) => {
    expect(toolGlyphKind(toolKind)).toBe(expected);
  });

  it("reads Claude command and file subjects from arguments", () => {
    expect(toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail: JSON.stringify({
        command: "ls -la /workspace",
        description: "List workspace"
      }),
      detail: "total 0"
    })).toEqual({ title: "Ran command", summary: "ls -la /workspace" });
    expect(toolCallLine({
      title: "Read",
      toolKind: "Read",
      startedDetail: '{"file_path":"/workspace/AGENTS.md"}',
      detail: null
    })).toEqual({ title: "Read file", summary: "/workspace/AGENTS.md" });
    expect(toolCallLine({
      title: "Write",
      toolKind: "Write",
      startedDetail: '{"content":"secret payload","file_path":"/workspace/note.md"}',
      detail: null
    })).toEqual({ title: "Edited file", summary: "/workspace/note.md" });
  });

  it("does not invent a subject from unknown argument fields", () => {
    expect(toolCallLine({
      title: "TaskUpdate",
      toolKind: "TaskUpdate",
      startedDetail: '{"status":"completed","taskId":"1"}',
      detail: null
    })).toEqual({ title: "TaskUpdate", summary: null });
    expect(toolCallLine({
      title: "AskUserQuestion",
      toolKind: "AskUserQuestion",
      startedDetail: '{"questions":[{"question":"Which colour?"}]}',
      detail: null
    })).toEqual({ title: "AskUserQuestion", summary: null });
  });

  it("removes Codex's shell wrapper from the visible command", () => {
    expect(toolCallLine({
      title: '/bin/zsh -lc "pwd && rg --files | head -25"',
      toolKind: "execute",
      startedDetail: "/workspace",
      detail: "/workspace"
    })).toEqual({
      title: "Ran command",
      summary: "pwd && rg --files | head -25"
    });
  });

  it("uses Hermes descriptive titles without repeating their kind", () => {
    expect(toolCallLine({
      title: "terminal: ls web/src/lib/conversation",
      toolKind: "execute",
      startedDetail: "$ ls web/src/lib/conversation",
      detail: "composer.ts\nfeed.ts"
    })).toEqual({
      title: "Ran command",
      summary: "ls web/src/lib/conversation"
    });
    expect(toolCallLine({
      title: "search: wire.ts",
      toolKind: "search",
      startedDetail: "Searching for 'wire.ts' (files) in /workspace",
      detail: null
    })).toEqual({ title: "Searched files", summary: "wire.ts" });
    expect(toolCallLine({
      title: "read: /workspace/wire.ts",
      toolKind: "read",
      startedDetail: null,
      detail: "line one\nline two"
    })).toEqual({ title: "Read file", summary: "/workspace/wire.ts" });
  });

  it("keeps summaries on one line and visibly truncates an overlong subject", () => {
    const line = toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail: JSON.stringify({ command: "x".repeat(100) }),
      detail: null
    });
    expect(line).toEqual({
      title: "Ran command",
      summary: `${"x".repeat(79)}…`
    });
    expect(toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail: JSON.stringify({ command: "cat <<'EOF' > note.txt\nhello\nEOF" }),
      detail: null
    }).summary).toBe("cat <<'EOF' > note.txt");
  });

  it.each([
    ['bash -c "npm test"', "npm test"],
    ["sh -c 'npm test'", "npm test"],
    ["/bin/zsh -lc 'npm test'", "npm test"],
    ["cmd /c dir", "dir"],
    ['pwsh -Command "Get-ChildItem"', "Get-ChildItem"],
    ['bash -c "echo \\"hi\\""', 'echo "hi"'],
    ["npm test", "npm test"],
    ["bashful -c thing", "bashful -c thing"]
  ])("unwraps command %s as %s", (command, expected) => {
    expect(commandWithoutShellInvocation(command)).toBe(expected);
  });

  it("keeps a summary when its letters merely occur inside the title", () => {
    expect(toolCallLine({
      title: "Grep",
      toolKind: "Grep",
      startedDetail: '{"pattern":"arch"}',
      detail: null
    })).toEqual({ title: "Searched files", summary: "arch" });
    expect(toolCallLine({
      title: "Grep",
      toolKind: "Grep",
      startedDetail: '{"pattern":"search"}',
      detail: null
    })).toEqual({ title: "Searched files", summary: "search" });
  });

  it("suppresses a disclosure only when the line shows the whole detail", () => {
    expect(lineShowsWholeDetail(
      { title: "Searched files", summary: "arch" },
      "arch"
    )).toBe(true);
    expect(lineShowsWholeDetail(
      { title: "Searched files", summary: null },
      "arch"
    )).toBe(false);

    const echoed = toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail: null,
      detail: "ls -la /tmp"
    });
    expect(echoed).toEqual({ title: "Ran command", summary: "ls -la /tmp" });
    expect(lineShowsWholeDetail(echoed, "ls -la /tmp")).toBe(true);
    expect(lineShowsWholeDetail(echoed, "ls -la /tmp\nfile one")).toBe(false);
  });
});
